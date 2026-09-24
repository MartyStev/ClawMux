"""
ClawMux — Mattermost Integration.

Connects to Mattermost WebSocket API v4 to:
- Listen for new messages (posted events)
- Send replies back to channels via HTTP API

Uses mattermostdriver for HTTP API and raw websockets for the event stream.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx
import structlog
import websockets

from src.core.config import settings
from src.services.chat_adapter import BaseChatAdapter, ChannelEvent
from src.services.mapping import DEFAULT_PROVIDER
from src.utils.tasks import fire_and_forget

logger = structlog.get_logger(__name__)


class MattermostEvent(ChannelEvent):
    """Parsed incoming Mattermost message event."""

    def __init__(
        self,
        user_id: str,
        channel_id: str,
        text: str,
        post_id: str,
        file_ids: list[str] | None = None,
        provider: str = DEFAULT_PROVIDER,
        root_id: str = "",
    ):
        super().__init__(
            user_id=user_id,
            channel_id=channel_id,
            text=text,
            post_id=post_id,
            file_ids=file_ids or [],
            provider=provider,
            root_id=root_id,
        )


class MattermostClient(BaseChatAdapter):
    """
    Mattermost integration layer implementing BaseChatAdapter.

    Responsibilities:
    - Connect to Mattermost WS API for real-time events
    - Parse 'posted' events → ChannelEvent
    - Send reply messages via HTTP API
    - Filter out bot's own messages
    """

    @property
    def name(self) -> str:
        return "mattermost"

    @property
    def is_connected(self) -> bool:
        return self.is_ws_connected

    def __init__(self) -> None:
        url = settings.mattermost_url.rstrip("/")
        self._http_client = httpx.AsyncClient(
            base_url=f"{url}/api/v4",
            headers={"Authorization": f"Bearer {settings.mattermost_token}"},
            timeout=10.0,
        )

        self._bot_user_id: str | None = None
        self._ws: websockets.ClientConnection | None = None
        self._running = False
        self._on_message: Callable[[MattermostEvent], Awaitable[None]] | None = None
        self._ws_seq = 1

    async def start(
        self,
        on_message: Callable[[MattermostEvent], Awaitable[None]],
    ) -> None:
        """
        Connect to Mattermost and start listening for events.

        Args:
            on_message: Async callback for each incoming user message.
        """
        self._on_message = on_message
        self._running = True

        # Get bot user info
        resp = await self._http_client.get("/users/me")
        resp.raise_for_status()
        me = resp.json()
        self._bot_user_id = me["id"]
        logger.info(
            "mattermost_logged_in",
            bot_user_id=self._bot_user_id,
            bot_username=me.get("username"),
        )

        # Start WS listener loop (auto-reconnect)
        while self._running:
            try:
                await self._ws_listen_loop()
            except Exception as e:
                if not self._running:
                    break
                logger.error("mattermost_ws_error", error=str(e))
                logger.info("mattermost_ws_reconnecting", delay_sec=5)
                await asyncio.sleep(5)

    async def _ws_listen_loop(self) -> None:
        """Single WS connection session — listen until disconnect."""
        # Build WS URL
        base = settings.mattermost_url.rstrip("/")
        ws_url = base.replace("https://", "wss://").replace("http://", "ws://")
        ws_url += "/api/v4/websocket"

        logger.info("mattermost_ws_connecting", url=ws_url)

        async with websockets.connect(ws_url) as ws:
            self._ws = ws

            # Authenticate the WS connection
            self._ws_seq += 1
            auth_msg = json.dumps(
                {
                    "seq": self._ws_seq,
                    "action": "authentication_challenge",
                    "data": {"token": settings.mattermost_token},
                }
            )
            await ws.send(auth_msg)
            logger.info("mattermost_ws_authenticated")

            # Listen for events
            async for raw in ws:
                if not self._running:
                    break

                try:
                    data = json.loads(raw)
                    await self._handle_ws_event(data)
                except json.JSONDecodeError:
                    logger.warning("mattermost_invalid_json", raw=raw[:200])
                except Exception as e:
                    logger.error("mattermost_event_handler_error", error=str(e))

    async def _handle_ws_event(self, data: dict) -> None:
        """Process a single Mattermost WS event."""
        event_type = data.get("event")

        # Only care about new posts
        if event_type != "posted":
            return

        event_data = data.get("data", {})
        post_str = event_data.get("post")
        if not post_str:
            return

        try:
            post = json.loads(post_str)
        except json.JSONDecodeError:
            return

        user_id = post.get("user_id", "")
        channel_id = post.get("channel_id", "")
        text = post.get("message", "")
        post_id = post.get("id", "")
        root_id = post.get("root_id", "")

        # Ignore bot's own messages
        if user_id == self._bot_user_id:
            return

        # Ignore truly empty events (no text AND no files)
        file_ids: list[str] = post.get("file_ids") or []
        if not text.strip() and not file_ids:
            return

        logger.info(
            "mattermost_message_received",
            user_id=user_id,
            channel_id=channel_id,
            text_len=len(text),
            post_id=post_id,
            root_id=root_id,
            file_ids_count=len(file_ids),
        )

        event = MattermostEvent(
            user_id=user_id,
            channel_id=channel_id,
            text=text,
            post_id=post_id,
            file_ids=file_ids,
            root_id=root_id,
        )

        # Dispatch to handler in a separate task so we don't block the WS read loop
        # Blocking this loop prevents websockets from replying to Ping frames.
        if self._on_message:

            async def _dispatch():
                try:
                    await self._on_message(event)
                except Exception as e:
                    logger.error("mattermost_on_message_error", error=str(e))

            fire_and_forget(_dispatch(), name="mm-on-message")

    async def send_typing(self, channel_id: str, parent_id: str = "") -> None:
        """
        Send a 'user_typing' event to Mattermost via WebSocket.
        This makes the 'Bot is typing...' indicator appear in the UI.
        """
        if self._ws is None:
            return

        try:
            if self._ws.protocol.state.name != "OPEN":
                return
            self._ws_seq += 1
            await self._ws.send(
                json.dumps(
                    {
                        "seq": self._ws_seq,
                        "action": "user_typing",
                        "data": {"channel_id": channel_id, "parent_id": parent_id},
                    }
                )
            )
        except Exception as e:
            logger.warning("mattermost_typing_error", error=str(e))

    async def send_reply(self, channel_id: str, message: str, root_id: str = "") -> str:
        """
        Send a reply message to a Mattermost channel.
        Returns the ID of the created post.

        Args:
            channel_id: Target channel.
            message:    Text to send.
            root_id:    If set, creates a threaded reply to that post ID.

        Uses the Mattermost HTTP API via the async HTTP client.
        """
        try:
            post_body: dict = {"channel_id": channel_id, "message": message}
            if root_id:
                post_body["root_id"] = root_id
            resp = await self._http_client.post("/posts", json=post_body)
            resp.raise_for_status()
            post = resp.json()
            logger.info(
                "mattermost_reply_sent",
                channel_id=channel_id,
                message_len=len(message),
                post_id=post.get("id"),
            )
            return post.get("id", "")
        except httpx.HTTPError as e:
            logger.error(
                "mattermost_reply_error",
                channel_id=channel_id,
                error=str(e),
            )
            raise

    async def send_post_with_files(
        self,
        channel_id: str,
        message: str,
        file_ids: list[str],
        root_id: str = "",
    ) -> str:
        """
        Send a Mattermost post with file attachments (Route B: OpenClaw → Mattermost).
        Returns the ID of the created post.

        Args:
            channel_id: Target channel.
            message:    Text body of the post.
            file_ids:   List of Mattermost file_ids to attach.
            root_id:    If set, creates a threaded reply.
        """
        try:
            post_body: dict = {
                "channel_id": channel_id,
                "message": message,
                "file_ids": file_ids,
            }
            if root_id:
                post_body["root_id"] = root_id
            resp = await self._http_client.post("/posts", json=post_body)
            resp.raise_for_status()
            post = resp.json()
            post_id = post.get("id", "")
            logger.info(
                "mattermost_post_with_files_sent",
                channel_id=channel_id,
                message_len=len(message),
                file_ids=file_ids,
                post_id=post_id,
            )
            return post_id
        except httpx.HTTPError as e:
            logger.error(
                "mattermost_post_with_files_error",
                channel_id=channel_id,
                file_ids=file_ids,
                error=str(e),
            )
            raise

    async def update_reply(self, post_id: str, message: str, channel_id: str = "") -> None:
        """
        Update an existing post in Mattermost. Used for streaming responses.
        """
        if not post_id:
            return
        try:
            post_body = {"id": post_id, "message": message}
            resp = await self._http_client.put(f"/posts/{post_id}", json=post_body)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("mattermost_update_error", post_id=post_id, error=str(e))

    async def get_or_create_dm_channel(self, user_id: str) -> str:
        """Create or get a Direct Message channel with the user."""
        if not self._bot_user_id:
            return ""
        try:
            resp = await self._http_client.post("/channels/direct", json=[self._bot_user_id, user_id])
            resp.raise_for_status()
            channel = resp.json()
            return channel.get("id", "")
        except httpx.HTTPError as e:
            logger.error("mattermost_dm_create_error", user_id=user_id, error=str(e))
            return ""

    @property
    def is_ws_connected(self) -> bool:
        """Check if WebSocket connection to Mattermost is active."""
        if not self._running or self._ws is None:
            return False
        try:
            if hasattr(self._ws, "protocol") and hasattr(self._ws.protocol, "state"):
                return self._ws.protocol.state.name == "OPEN"
            return getattr(self._ws, "open", not getattr(self._ws, "closed", True))
        except Exception:
            return False

    async def stop(self) -> None:
        """Stop listening and disconnect."""
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if hasattr(self, "_http_client"):
            await self._http_client.aclose()
        logger.info("mattermost_stopped")


# Alias for consistent naming
MattermostAdapter = MattermostClient
