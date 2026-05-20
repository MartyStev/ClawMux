from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.notify import router as notify_router
from src.api.trigger import router as trigger_router
from src.services.mapping import (
    DeviceCredentials,
    InstanceInfo,
    UnsupportedProviderError,
)


class FakeMapping:
    def __init__(self, *, unsupported: bool = False):
        self.unsupported = unsupported
        self.calls: list[tuple[str, str]] = []

    async def get_instance_by_external_id(
        self,
        external_user_id: str,
        provider: str = "mattermost",
    ) -> tuple[str, InstanceInfo]:
        self.calls.append((external_user_id, provider))
        if self.unsupported:
            raise UnsupportedProviderError(provider)
        return "provider-user-1", InstanceInfo(
            instance_url="ws://openclaw-gw-test:18789/ws",
            credentials=DeviceCredentials(
                device_id="device",
                public_key_b64="public",
                private_key_b64="private",
                device_token="device-token",
                gateway_token="gateway-token",
            ),
        )


class FakeRouter:
    def __init__(self):
        self.trigger_calls: list[dict] = []
        self.proactive_calls: list[dict] = []

    async def trigger_message(self, **kwargs) -> None:
        self.trigger_calls.append(kwargs)

    async def handle_proactive(self, **kwargs) -> None:
        self.proactive_calls.append(kwargs)


def make_client(mapping: FakeMapping, router: FakeRouter) -> TestClient:
    app = FastAPI()
    app.include_router(trigger_router)
    app.include_router(notify_router)
    app.state.mapping = mapping
    app.state.router = router
    app.state.ws_manager = object()
    return TestClient(app)


def test_trigger_dispatches_with_provider(monkeypatch) -> None:
    monkeypatch.setattr("src.api.trigger.settings.api_token", "secret")
    mapping = FakeMapping()
    router = FakeRouter()
    client = make_client(mapping, router)

    response = client.post(
        "/api/v1/trigger",
        headers={"x-api-token": "secret"},
        json={
            "external_user_id": "ext-1",
            "provider": "mattermost",
            "text": "run report",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert mapping.calls == [("ext-1", "mattermost")]


def test_notify_rejects_unsupported_provider(monkeypatch) -> None:
    monkeypatch.setattr("src.api.notify.settings.api_token", "secret")
    mapping = FakeMapping(unsupported=True)
    router = FakeRouter()
    client = make_client(mapping, router)

    response = client.post(
        "/api/v1/notify",
        headers={"x-api-token": "secret"},
        json={
            "external_user_id": "ext-1",
            "provider": "slack",
            "text": "hello",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Provider is not enabled: 'slack'"
