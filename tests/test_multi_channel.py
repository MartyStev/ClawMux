import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.bitrix import router as bitrix_router
from src.api.teams import router as teams_router
from src.router import Router
from src.services.chat_adapter import BaseChatAdapter, ChannelEvent, ProviderRegistry
from src.services.bitrix import BitrixAdapter
from src.services.mapping import DeviceCredentials, InstanceInfo
from src.services.telegram import TelegramAdapter
from src.services.slack import SlackAdapter
from src.services.vk_teams import VkTeamsAdapter
from src.services.teams import TeamsAdapter


class DummyAdapter(BaseChatAdapter):
    def __init__(self, name: str):
        self._name = name
        self.replies: list[tuple[str, str, str]] = []
        self.typing_calls: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_connected(self) -> bool:
        return True

    async def start(self, on_message) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send_reply(self, channel_id: str, message: str, root_id: str = "") -> str:
        self.replies.append((channel_id, message, root_id))
        return "msg-123"

    async def update_reply(self, post_id: str, message: str, channel_id: str = "") -> None:
        pass

    async def send_typing(self, channel_id: str, parent_id: str = "") -> None:
        self.typing_calls.append((channel_id, parent_id))

    async def send_post_with_files(self, channel_id: str, message: str, file_ids_or_paths: list[str], root_id: str = "") -> str:
        return "file-msg-123"

    async def get_or_create_dm_channel(self, user_id: str) -> str:
        return f"dm-{user_id}"


def test_provider_registry():
    registry = ProviderRegistry()
    tg = DummyAdapter("telegram")
    bx = DummyAdapter("bitrix")

    registry.register(tg)
    registry.register(bx)

    assert registry.is_registered("telegram")
    assert registry.is_registered("bitrix")
    assert not registry.is_registered("slack")
    assert registry.supported_providers() == {"telegram", "bitrix"}
    assert registry.get("TELEGRAM") == tg
    assert len(registry.all()) == 2


def test_router_dispatches_to_correct_provider_adapter():
    registry = ProviderRegistry()
    tg = DummyAdapter("telegram")
    bx = DummyAdapter("bitrix")
    mm = DummyAdapter("mattermost")

    registry.register(tg)
    registry.register(bx)
    registry.register(mm)

    mapping = AsyncMock()
    mapping.get_instance_by_identity.return_value = InstanceInfo(
        instance_url="ws://test:123/ws",
        credentials=DeviceCredentials(
            device_id="dev",
            public_key_b64="pub",
            private_key_b64="priv",
            device_token="dt",
            gateway_token="gt",
        ),
    )
    ws_manager = MagicMock()
    ws_manager.send_message = AsyncMock(return_value=("agent answer", []))
    ws_manager.get_cached_info.return_value = None

    router = Router(mapping=mapping, ws_manager=ws_manager, providers=registry)
    router._typing_loop = AsyncMock()

    # Send Telegram event
    tg_event = ChannelEvent(
        provider="telegram",
        user_id="tg_user_100",
        channel_id="tg_chat_200",
        post_id="tg_msg_1",
        text="Hello from Telegram",
        root_id="12345",  # topic id
    )

    asyncio.run(router.handle_event(tg_event))

    # Verification
    mapping.get_instance_by_identity.assert_called_with("telegram", "tg_user_100")
    ws_manager.send_message.assert_called_once()
    # Replies must be sent via Telegram adapter with root_id preserved
    assert len(tg.replies) >= 1
    assert tg.replies[0][0] == "tg_chat_200"
    assert tg.replies[0][2] == "12345"
    assert len(bx.replies) == 0


def test_bitrix_webhook_endpoint(monkeypatch):
    monkeypatch.setattr("src.api.bitrix.settings.bitrix_inbound_secret", "test-secret")
    app = FastAPI()
    app.include_router(bitrix_router)
    
    router_mock = AsyncMock()
    app.state.router = router_mock
    client = TestClient(app)

    payload = {
        "auth": {"access_token": "test-secret"},
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

    resp = client.post("/api/v1/bitrix/event", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"


@pytest.mark.anyio
async def test_telegram_send_reply():
    tg = TelegramAdapter(bot_token="123456:ABC-DEF")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"ok": True, "result": {"message_id": 999}}

    tg._http_client.post = AsyncMock(return_value=mock_response)

    msg_id = await tg.send_reply(channel_id="12345", message="Test response", root_id="77")
    assert msg_id == "999"
    tg._http_client.post.assert_called_once()
    call_args = tg._http_client.post.call_args[1]["json"]
    assert call_args["chat_id"] == "12345"
    assert call_args["text"] == "Test response"
    assert call_args["message_thread_id"] == 77
    await tg.stop()


@pytest.mark.anyio
async def test_bitrix_send_reply():
    bx = BitrixAdapter(webhook_url="https://portal.bitrix24.com/rest/1/secret_token", bot_id=10)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"result": 5555}

    bx._http_client.post = AsyncMock(return_value=mock_response)

    msg_id = await bx.send_reply(channel_id="user_7", message="Test for Bitrix")
    assert msg_id == "5555"
    call_args = bx._http_client.post.call_args[1]["json"]
    assert call_args["DIALOG_ID"] == "user_7"
    assert call_args["MESSAGE"] == "Test for Bitrix"
    assert call_args["BOT_ID"] == 10
    await bx.stop()


@pytest.mark.anyio
async def test_slack_send_reply():
    slack = SlackAdapter(bot_token="xoxb-test-token", app_token="xapp-test-app")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"ok": True, "ts": "1710000000.123456"}

    slack._http_client.post = AsyncMock(return_value=mock_resp)

    msg_id = await slack.send_reply(channel_id="C123456", message="Hello Slack", root_id="1710000000.000001")
    assert msg_id == "1710000000.123456"
    call_args = slack._http_client.post.call_args[1]["json"]
    assert call_args["channel"] == "C123456"
    assert call_args["text"] == "Hello Slack"
    assert call_args["thread_ts"] == "1710000000.000001"
    await slack.stop()


@pytest.mark.anyio
async def test_vk_teams_send_reply():
    vk = VkTeamsAdapter(bot_token="001.test.token")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"ok": True, "msgId": "vk_msg_789"}

    vk._http_client.get = AsyncMock(return_value=mock_resp)

    msg_id = await vk.send_reply(channel_id="chat_123", message="Hello VK", root_id="orig_456")
    assert msg_id == "vk_msg_789"
    call_args = vk._http_client.get.call_args[1]["params"]
    assert call_args["chatId"] == "chat_123"
    assert call_args["text"] == "Hello VK"
    assert call_args["replyMsgId"] == "orig_456"
    await vk.stop()


@pytest.mark.anyio
async def test_teams_send_reply():
    teams = TeamsAdapter(app_id="app-123", app_password="secret-pass")
    teams._access_token = "mock-jwt-token"
    teams._token_expires_at = 9999999999.0

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"id": "act-999"}

    teams._http_client.post = AsyncMock(return_value=mock_resp)

    msg_id = await teams.send_reply(channel_id="conv-123", message="Hello Teams", root_id="act-parent")
    assert msg_id == "act-999"
    call_args = teams._http_client.post.call_args[1]["json"]
    assert call_args["text"] == "Hello Teams"
    assert call_args["replyToId"] == "act-parent"
    await teams.stop()


def test_teams_webhook_endpoint(monkeypatch):
    # JWT verification itself is covered in tests/test_security_fixes.py
    monkeypatch.setattr("src.api.teams.verify_inbound_token", lambda authorization: None)
    app = FastAPI()
    app.include_router(teams_router)

    mock_router = MagicMock()
    mock_router.handle_event = AsyncMock()
    app.state.router = mock_router

    mock_adapter = MagicMock(spec=TeamsAdapter)
    mock_registry = ProviderRegistry()
    mock_registry.register(mock_adapter)
    app.state.registry = mock_registry

    client = TestClient(app)

    payload = {
        "type": "message",
        "id": "act-555",
        "serviceUrl": "https://smba.trafficmanager.net/teams/",
        "from": {"id": "user-corp-1", "name": "Alice"},
        "conversation": {"id": "conv-group-1"},
        "text": "Hello from Teams bot",
        "replyToId": "thread-root-1",
    }

    resp = client.post("/api/v1/teams/messages", json=payload)
    assert resp.status_code == 200
