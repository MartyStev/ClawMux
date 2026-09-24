"""
Tests for the security fixes:
  - Teams inbound webhook: JWT verification + serviceUrl whitelist (anti-SSRF)
  - Bitrix inbound webhook: shared secret check
  - Mattermost interactive action proxy: shared secret check
  - OpenClaw credentials: Fernet encryption at rest (real SQLite DB, no mocks)
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.config import settings
from src.core.crypto import PREFIX, encrypt_secret, is_encrypted

# ── Teams: JWT verification ─────────────────────────────────────────


@pytest.fixture()
def teams_settings(monkeypatch):
    monkeypatch.setattr(settings, "teams_app_id", "bot-app-id")
    monkeypatch.setattr(settings, "teams_jwt_audience", "")
    monkeypatch.setattr(settings, "teams_allowed_service_url_hosts", "smba.trafficmanager.net")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key


class _FakeJwkClient:
    def __init__(self, public_key):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):  # noqa: ARG002
        holder = MagicMock()
        holder.key = self._public_key
        return holder


def _make_token(private_key, *, audience="bot-app-id", issuer="https://api.botframework.com"):
    return jwt.encode(
        {
            "aud": audience,
            "iss": issuer,
            "sub": "user-1",
            "exp": int(time.time()) + 3600,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key-1"},
    )


def _teams_app():
    from src.api.teams import router as teams_router

    app = FastAPI()
    app.include_router(teams_router)
    mock_router = MagicMock()
    mock_router.handle_event = AsyncMock()
    app.state.router = mock_router
    registry_mock = MagicMock()
    registry_mock.get.return_value = None
    app.state.registry = registry_mock
    return app, mock_router


def _teams_payload(service_url="https://smba.trafficmanager.net/teams/"):
    return {
        "type": "message",
        "id": "act-555",
        "serviceUrl": service_url,
        "from": {"id": "user-corp-1", "name": "Alice"},
        "conversation": {"id": "conv-group-1"},
        "text": "Hello from Teams bot",
    }


def test_teams_rejects_missing_token(teams_settings, monkeypatch):
    from src.services import teams_auth

    monkeypatch.setattr(teams_auth, "_jwk_clients", {})
    app, _ = _teams_app()
    client = TestClient(app)
    resp = client.post("/api/v1/teams/messages", json=_teams_payload())
    assert resp.status_code == 401


def test_teams_rejects_forged_token(teams_settings, monkeypatch):
    # Attacker signs with their own key — not trusted via JWKS path
    from src.services import teams_auth

    forged = _make_token(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    client_jwk = _FakeJwkClient(teams_settings.public_key())  # trust the legit key only

    def fake_client(url):  # noqa: ARG001
        return client_jwk

    monkeypatch.setattr(teams_auth, "_get_jwk_client", fake_client)
    app, _ = _teams_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/teams/messages",
        json=_teams_payload(),
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert resp.status_code == 401


def test_teams_accepts_valid_token(teams_settings, monkeypatch):
    from src.services import teams_auth

    client_jwk = _FakeJwkClient(teams_settings.public_key())
    monkeypatch.setattr(teams_auth, "_get_jwk_client", lambda url: client_jwk)
    token = _make_token(teams_settings)

    app, mock_router = _teams_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/teams/messages",
        json=_teams_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert mock_router.handle_event.called


def test_teams_rejects_wrong_audience(teams_settings, monkeypatch):
    from src.services import teams_auth

    client_jwk = _FakeJwkClient(teams_settings.public_key())
    monkeypatch.setattr(teams_auth, "_get_jwk_client", lambda url: client_jwk)
    token = _make_token(teams_settings, audience="someone-elses-bot")

    app, _ = _teams_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/teams/messages",
        json=_teams_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_teams_rejects_evil_service_url(teams_settings, monkeypatch):
    from src.services import teams_auth

    client_jwk = _FakeJwkClient(teams_settings.public_key())
    monkeypatch.setattr(teams_auth, "_get_jwk_client", lambda url: client_jwk)
    token = _make_token(teams_settings)

    app, mock_router = _teams_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/teams/messages",
        json=_teams_payload(service_url="https://evil.example.com/"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert not mock_router.handle_event.called


def test_service_url_whitelist_rules(monkeypatch):
    from src.services.teams_auth import is_allowed_service_url

    monkeypatch.setattr(settings, "teams_allowed_service_url_hosts", "smba.trafficmanager.net")
    assert is_allowed_service_url("https://smba.trafficmanager.net/teams/")
    assert is_allowed_service_url("https://sub.smba.trafficmanager.net/x")
    assert not is_allowed_service_url("http://smba.trafficmanager.net/")  # no TLS
    assert not is_allowed_service_url("https://smba.trafficmanager.net.evil.com/")
    assert not is_allowed_service_url("https://evil.com/smba.trafficmanager.net")
    assert not is_allowed_service_url("")


def test_teams_adapter_refuses_bad_service_url(monkeypatch):
    from src.services.teams import TeamsAdapter

    monkeypatch.setattr(settings, "teams_allowed_service_url_hosts", "smba.trafficmanager.net")
    adapter = TeamsAdapter(app_id="a", app_password="b")
    adapter.register_service_url("conv-1", "https://evil.example.com/")
    assert adapter.get_service_url("conv-1") == "https://smba.trafficmanager.net/teams"
    adapter.register_service_url("conv-2", "https://smba.trafficmanager.net/teams/")
    assert adapter.get_service_url("conv-2") == "https://smba.trafficmanager.net/teams"


# ── Bitrix: inbound secret ──────────────────────────────────────────


def _bitrix_app():
    from src.api.bitrix import router as bitrix_router

    app = FastAPI()
    app.include_router(bitrix_router)
    router_mock = MagicMock()
    router_mock.handle_event = AsyncMock()
    app.state.router = router_mock
    return app


_BITRIX_PAYLOAD = {
    "event": "ONIMBOTMESSAGEADD",
    "data": {
        "PARAMS": {
            "FROM_USER_ID": "42",
            "DIALOG_ID": "chat99",
            "MESSAGE": "Hello Bitrix",
            "MESSAGE_ID": "1001",
        }
    },
}


def test_bitrix_disabled_without_secret(monkeypatch):
    monkeypatch.setattr(settings, "bitrix_inbound_secret", "")
    client = TestClient(_bitrix_app())
    resp = client.post("/api/v1/bitrix/event", json=_BITRIX_PAYLOAD)
    assert resp.status_code == 401


def test_bitrix_rejects_wrong_secret(monkeypatch):
    monkeypatch.setattr(settings, "bitrix_inbound_secret", "topsecret")
    payload = dict(_BITRIX_PAYLOAD, auth={"access_token": "wrong"})
    client = TestClient(_bitrix_app())
    resp = client.post("/api/v1/bitrix/event", json=payload)
    assert resp.status_code == 401


def test_bitrix_accepts_auth_token(monkeypatch):
    monkeypatch.setattr(settings, "bitrix_inbound_secret", "topsecret")
    payload = dict(_BITRIX_PAYLOAD, auth={"access_token": "topsecret"})
    client = TestClient(_bitrix_app())
    resp = client.post("/api/v1/bitrix/event", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"


def test_bitrix_accepts_query_secure_param(monkeypatch):
    monkeypatch.setattr(settings, "bitrix_inbound_secret", "topsecret")
    client = TestClient(_bitrix_app())
    resp = client.post("/api/v1/bitrix/event?secure=topsecret", json=_BITRIX_PAYLOAD)
    assert resp.status_code == 200


# ── mm/action: shared secret ────────────────────────────────────────


def _mm_action_app(monkeypatch, forwarded):
    from src.api.mm_action import router as mm_router

    async def fake_post(self, url, json=None, timeout=None):  # noqa: ANN001
        forwarded["url"] = url
        forwarded["payload"] = json
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    app = FastAPI()
    app.include_router(mm_router)
    return app


def test_mm_action_disabled_without_secret(monkeypatch):
    monkeypatch.setattr(settings, "mm_action_shared_secret", "")
    client = TestClient(_mm_action_app(monkeypatch, {}))
    resp = client.post("/api/v1/mm/action", json={"context": {}})
    assert resp.status_code == 401


def test_mm_action_rejects_wrong_secret(monkeypatch):
    monkeypatch.setattr(settings, "mm_action_shared_secret", "hmacme")
    client = TestClient(_mm_action_app(monkeypatch, {}))
    resp = client.post(
        "/api/v1/mm/action",
        json={"context": {}},
        headers={"X-MM-Action-Secret": "nope"},
    )
    assert resp.status_code == 401


def test_mm_action_forwards_with_header_secret(monkeypatch):
    monkeypatch.setattr(settings, "mm_action_shared_secret", "hmacme")
    monkeypatch.setattr(settings, "mm_action_proxy_url", "http://tools-server:3000/mm/action")
    forwarded = {}
    client = TestClient(_mm_action_app(monkeypatch, forwarded))
    resp = client.post(
        "/api/v1/mm/action",
        json={"context": {"task_id": 7}},
        headers={"X-MM-Action-Secret": "hmacme"},
    )
    assert resp.status_code == 200
    assert forwarded["url"] == "http://tools-server:3000/mm/action"
    assert forwarded["payload"]["context"]["task_id"] == 7


def test_mm_action_forwards_with_query_secret(monkeypatch):
    monkeypatch.setattr(settings, "mm_action_shared_secret", "hmacme")
    monkeypatch.setattr(settings, "mm_action_proxy_url", "http://tools-server:3000/mm/action")
    forwarded = {}
    client = TestClient(_mm_action_app(monkeypatch, forwarded))
    resp = client.post("/api/v1/mm/action?secret=hmacme", json={"context": {}})
    assert resp.status_code == 200


# ── Credential encryption at rest (real SQLite database) ────────────


def _creds():
    from src.services.mapping import DeviceCredentials

    return DeviceCredentials(
        device_id="dev-123",
        public_key_b64="PUBkey",
        private_key_b64="PRIVkey-s3cr3t",
        device_token="DEVtoken",
        gateway_token="GWtoken",
    )


def test_credentials_encrypted_at_rest(sqlite_db, monkeypatch):
    # A real Fernet key: generate one for the test
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())

    from sqlalchemy import text

    from src.services.mapping import MappingStorage

    storage = MappingStorage()
    asyncio.run(
        storage.bind_user_instance(
            provider="mattermost",
            provider_user_id="user-enc",
            instance_uuid="11111111-2222-3333-4444-555555555555",
            instance_url="ws://openclaw-gw-test:18789/ws",
            credentials=_creds(),
        )
    )

    async def _raw_values():
        async with sqlite_db() as session:
            row = (
                await session.execute(
                    text("SELECT private_key_b64, device_token, gateway_token, public_key_b64 FROM instance")
                )
            ).one()
        return row

    private_raw, device_raw, gateway_raw, public_raw = asyncio.run(_raw_values())
    assert private_raw.startswith(PREFIX) and "PRIVkey" not in private_raw
    assert device_raw.startswith(PREFIX) and "DEVtoken" not in device_raw
    assert gateway_raw.startswith(PREFIX) and "GWtoken" not in gateway_raw
    # public key is not a secret — stored as-is
    assert public_raw == "PUBkey"

    # ORM read returns decrypted values
    info = asyncio.run(storage.get_instance_by_identity("mattermost", "user-enc"))
    assert info.credentials.private_key_b64 == "PRIVkey-s3cr3t"
    assert info.credentials.device_token == "DEVtoken"
    assert info.credentials.gateway_token == "GWtoken"


def test_plaintext_passthrough_without_key(monkeypatch):
    monkeypatch.setattr(settings, "credential_encryption_key", "")
    assert encrypt_secret("raw-value") == "raw-value"
    assert not is_encrypted("raw-value")


def test_encrypted_value_needs_matching_key(monkeypatch):
    from cryptography.fernet import Fernet

    from src.core.crypto import CredentialEncryptionError, decrypt_secret

    key_a = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "credential_encryption_key", key_a)
    encrypted = encrypt_secret("secret-stuff")
    assert is_encrypted(encrypted)

    # Decryption with the wrong key must fail loudly
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    with pytest.raises(CredentialEncryptionError):
        decrypt_secret(encrypted)


def test_legacy_plaintext_still_readable(monkeypatch):
    from src.core.crypto import decrypt_secret

    monkeypatch.setattr(settings, "credential_encryption_key", "anything")
    assert decrypt_secret("legacy-plaintext-row") == "legacy-plaintext-row"
