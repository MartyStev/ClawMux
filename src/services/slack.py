"""
ClawMux — Slack Channel Adapter (Socket Mode & REST).

Connects to Slack via Socket Mode (WebSocket) to receive events without exposing a public webhook,
and uses Slack REST API for posting and updating messages (streaming responses).
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog
import websockets

from src.services.chat_adapter import BaseChatAdapter, ChannelEvent
from src.utils.tasks import fire_and_forget

logger = structlog.get_logger(__name__)


class SlackAdapter(BaseChatAdapter):
    """
    Slack adapter implementing BaseChatAdapter using Socket Mode and Web API.
    """

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        api_url: str = "https://slack.com/api",
    ) -> None:
        self._bot_token = bot_token.strip()
        self._app_token = app_token.strip()
        self._api_url = api_url.rstrip("/")
        self._http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._bot_token}"},
            timeout=30.0,
        )
        self._running = False
        self._ws_task: asyncio.Task | None = None
        self._ws_connected = False
        self._bot_user_id = ""
        self._on_message: Callable[[ChannelEvent], Awaitable[None]] | None = None

    @property
    def name(self) -> str:
        return "slack"

    @property
    def is_connected(self) -> bool:
        return self._running and self._ws_connected

    async def start(
        self,
        on_message: Callable[[ChannelEvent], Awaitable[None]],
    ) -> None:
        """Verify tokens and start background Socket Mode WebSocket listener."""
        self._on_message = on_message
        self._running = True

        # 1. Verify bot token & fetch bot user id
        try:
            resp = await self._http_client.post(f"{self._api_url}/auth.test")
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack auth.test failed: {data.get('error')}")
            self._bot_user_id = data.get("user_id", "")
            logger.info("slack_bot_authenticated", bot_user_id=self._bot_user_id, team=data.get("team"))
        except Exception as e:
            logger.error("slack_auth_error", error=str(e))
            raise

        # 2. Start Socket Mode loop
        self._ws_task = asyncio.create_task(self._socket_mode_loop(), name="slack-socket-mode")

    async def stop(self) -> None:
        """Stop adapter and close HTTP client."""
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        await self._http_client.aclose()
        self._ws_connected = False
        logger.info("slack_adapter_stopped")

    async def _get_wss_url(self) -> str:
        """Obtain a WebSocket URL using the App-Level Token (xapp-...)."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self._api_url}/apps.connections.open",
                headers={"Authorization": f"Bearer {self._app_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack apps.connections.open failed: {data.get('error')}")
            return data["url"]

    async def _socket_mode_loop(self) -> None:
        """Maintain persistent WebSocket connection to Slack Socket Mode."""
        reconnect_delay = 1.0
        while self._running:
            try:
                wss_url = await self._get_wss_url()
                async with websockets.connect(wss_url, ping_interval=30, ping_timeout=10) as ws:
                    self._ws_connected = True
                    reconnect_delay = 1.0
                    logger.info("slack_socket_mode_connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            envelope_id = msg.get("envelope_id")
                            msg_type = msg.get("type")

                            # Acknowledge envelope immediately if required
                            if envelope_id:
                                await ws.send(json.dumps({"envelope_id": envelope_id}))

                            if msg_type == "events_api":
                                await self._handle_events_api(msg.get("payload", {}))
                            elif msg_type == "slash_commands":
                                pass
                        except json.JSONDecodeError:
                            logger.warning("slack_invalid_json", raw=raw[:100])
                        except Exception as e:
                            logger.error("slack_event_dispatch_error", error=str(e))

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._ws_connected = False
                logger.warning("slack_ws_connection_lost", error=str(e), next_retry_sec=reconnect_delay)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60.0)

    async def _handle_events_api(self, payload: dict[str, Any]) -> None:
        """Process an Events API payload from Socket Mode."""
        event = payload.get("event", {})
        event_type = event.get("type")
        subtype = event.get("subtype")

        # Ignore bot's own messages or message edits / deletes
        if subtype in ("bot_message", "message_deleted", "message_changed"):
            return

        user_id = event.get("user")
        if not user_id or user_id == self._bot_user_id:
            return

        # Supported events: message, app_mention
        if event_type in ("message", "app_mention"):
            channel_id = event.get("channel", "")
            text = (event.get("text") or "").strip()
            ts = event.get("ts", "")
            # Thread root timestamp in Slack
            thread_ts = event.get("thread_ts") or ""

            # Check for file attachments
            files = event.get("files", [])
            file_ids = [f.get("id") for f in files if f.get("id")]

            channel_event = ChannelEvent(
                provider="slack",
                user_id=user_id,
                channel_id=channel_id,
                text=text,
                post_id=ts,
                file_ids=file_ids,
                root_id=thread_ts,
            )

            if self._on_message:
                fire_and_forget(self._on_message(channel_event), name="slack-on-message")

    async def send_reply(self, channel_id: str, message: str, root_id: str = "") -> str:
        """Send a message to a Slack channel or thread."""
        body: dict[str, Any] = {
            "channel": channel_id,
            "text": message,
        }
        if root_id:
            body["thread_ts"] = root_id

        try:
            resp = await self._http_client.post(f"{self._api_url}/chat.postMessage", json=body)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error("slack_send_reply_failed", error=data.get("error"), channel=channel_id)
                return ""
            return data.get("ts", "")
        except Exception as e:
            logger.error("slack_send_reply_error", error=str(e), channel=channel_id)
            return ""

    async def update_reply(self, post_id: str, message: str, channel_id: str = "") -> None:
        """Update an existing message for streaming text."""
        if not post_id or not channel_id:
            return

        body = {
            "channel": channel_id,
            "ts": post_id,
            "text": message,
        }
        try:
            resp = await self._http_client.post(f"{self._api_url}/chat.update", json=body)
            resp.raise_for_status()
        except Exception as e:
            logger.debug("slack_update_reply_error", error=str(e), post_id=post_id)

    async def send_typing(self, channel_id: str, parent_id: str = "") -> None:
        """Slack Bot API does not provide a REST typing indicator; handled as no-op."""
        pass

    async def send_post_with_files(
        self,
        channel_id: str,
        message: str,
        file_ids_or_paths: list[str],
        root_id: str = "",
    ) -> str:
        """Send message with files or download links to Slack."""
        text = message
        if file_ids_or_paths:
            file_notes = "\n".join([f"📎 Attachment: {p}" for p in file_ids_or_paths])
            text = f"{text}\n\n{file_notes}" if text else file_notes
        return await self.send_reply(channel_id=channel_id, message=text, root_id=root_id)

    async def get_or_create_dm_channel(self, user_id: str) -> str:
        """Open or get a direct message conversation with a user."""
        try:
            resp = await self._http_client.post(
                f"{self._api_url}/conversations.open",
                json={"users": user_id},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                return data.get("channel", {}).get("id", "")
        except Exception as e:
            logger.error("slack_open_dm_failed", user_id=user_id, error=str(e))
        return ""
