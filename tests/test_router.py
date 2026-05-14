import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock

from src.router import Router
from src.services.mapping import InstanceInfo, InstanceNotFoundError, DeviceCredentials
from src.services.mattermost import MattermostEvent


def test_trigger_message_sends_to_ws_manager():
    """Test that trigger_message calls ws_manager.send_message with correct params."""
    # Mocks
    mapping = MagicMock()
    ws_manager = AsyncMock()
    ws_manager.send_message.return_value = ("response text", [])
    mattermost = AsyncMock()

    router = Router(mapping, ws_manager, mattermost)
    router.get_or_create_channel = AsyncMock(return_value="channel-123")

    info = InstanceInfo(
        instance_url="ws://test:123/ws",
        credentials=DeviceCredentials(
            device_id="dev",
            public_key_b64="pub",
            private_key_b64="priv",
            device_token="dt",
            gateway_token="gt",
        ),
    )

    asyncio.run(router.trigger_message(
        user_id="user-1",
        info=info,
        text="test message",
        session_key="test:session",
    ))

    ws_manager.send_message.assert_called_once_with(
        user_id="mattermost:user-1",
        info=info,
        message="test message",
        session_key="test:session",
        on_stream=None,
    )
    mattermost.send_reply.assert_called_once_with("channel-123", "response text")


def test_handle_event_routes_message():
    """Test that handle_event processes a Mattermost event and routes to OpenClaw."""
    # Mocks
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
    ws_manager.send_message = AsyncMock(return_value=("openclaw response", []))
    ws_manager.get_cached_info.return_value = None
    mattermost = AsyncMock()
    mattermost.send_reply.return_value = "post-123"

    router = Router(mapping, ws_manager, mattermost)
    router._typing_loop = AsyncMock()

    event = MattermostEvent(
        provider="mattermost",
        user_id="user-1",
        channel_id="chan-1",
        post_id="post-1",
        text="hello openclaw",
        file_ids=[],
    )

    asyncio.run(router.handle_event(event))

    mapping.get_instance_by_identity.assert_called_once_with("mattermost", "user-1")
    ws_manager.send_message.assert_called_once()
    mattermost.send_reply.assert_called()


def test_handle_event_fallback_to_dify():
    """Test that handle_event falls back to Dify when no instance found."""
    # Mocks
    mapping = AsyncMock()
    mapping.get_instance_by_identity.side_effect = InstanceNotFoundError("mattermost:user-1")
    ws_manager = MagicMock()
    ws_manager.get_cached_info.return_value = None
    mattermost = AsyncMock()

    router = Router(mapping, ws_manager, mattermost)
    router._dify = AsyncMock()
    router._handle_dify_fallback = AsyncMock()

    event = MattermostEvent(
        provider="mattermost",
        user_id="user-1",
        channel_id="chan-1",
        post_id="post-1",
        text="hello",
        file_ids=[],
    )

    asyncio.run(router.handle_event(event))

    router._handle_dify_fallback.assert_called_once_with(event, ANY)