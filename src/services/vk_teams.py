"""
ClawMux — VK Teams (Myteam) Channel Adapter.

Connects to VK Teams (Myteam) Bot API using httpx to:
- Poll for incoming messages via events/get (Long Polling)
- Route incoming messages to OpenClaw instances
- Send replies, edit streaming answers, and send typing indicators
"""

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
import structlog

from src.services.chat_adapter import BaseChatAdapter, ChannelEvent
from src.utils.tasks import fire_and_forget

logger = structlog.get_logger(__name__)


class VkTeamsAdapter(BaseChatAdapter):
    """
    VK Teams / Myteam Bot API adapter implementing BaseChatAdapter.
    """

    def __init__(
        self,
        bot_token: str,
        api_url: str = "https://myteam.mail.ru/bot/v1",
    ) -> None:
        self._token = bot_token.strip()
        self._api_url = api_url.rstrip("/")
        self._http_client = httpx.AsyncClient(timeout=35.0)
        self._running = False
        self._listen_task: Optional[asyncio.Task] = None
        self._last_event_id = 0
        self._bot_user_id = ""
        self._on_message: Optional[Callable[[ChannelEvent], Awaitable[None]]] = None

    @property
    def name(self) -> str:
        return "vk_teams"

    @property
    def is_connected(self) -> bool:
        return self._running and bool(self._bot_user_id)

    async def start(
        self,
        on_message: Callable[[ChannelEvent], Awaitable[None]],
    ) -> None:
        """Connect to VK Teams API and start long polling for events."""
        self._on_message = on_message
        self._running = True

        # Fetch bot user info to verify token (self/get)
        try:
            resp = await self._http_client.get(
                f"{self._api_url}/self/get",
                params={"token": self._token},
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"VK Teams self/get failed: {data.get('description')}")
            self._bot_user_id = str(data.get("userId") or data.get("userId", ""))
            logger.info(
                "vk_teams_logged_in",
                bot_id=self._bot_user_id,
                nickname=data.get("nick"),
            )
        except Exception as e:
            logger.error("vk_teams_login_failed", error=str(e))
            raise

        self._listen_task = asyncio.create_task(
            self._poll_loop(),
            name="vk-teams-polling",
        )

    async def stop(self) -> None:
        """Stop adapter and cleanup resources."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        await self._http_client.aclose()
        logger.info("vk_teams_stopped")

    async def _poll_loop(self) -> None:
        """Continuously long-poll for updates from VK Teams."""
        while self._running:
            try:
                params = {
                    "token": self._token,
                    "lastEventId": self._last_event_id,
                    "pollTime": 25,
                }
                resp = await self._http_client.get(
                    f"{self._api_url}/events/get",
                    params=params,
                    timeout=35.0,
                )
                if resp.status_code != 200:
                    logger.warning("vk_teams_poll_error", status_code=resp.status_code)
                    await asyncio.sleep(3)
                    continue

                data = resp.json()
                events = data.get("events", [])
                for ev in events:
                    self._last_event_id = ev.get("eventId", self._last_event_id)
                    await self._handle_event(ev)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("vk_teams_poll_exception", error=str(e))
                await asyncio.sleep(3)

    async def _handle_event(self, event_data: Dict[str, Any]) -> None:
        """Process a single event from VK Teams."""
        ev_type = event_data.get("type")
        payload = event_data.get("payload", {})

        if ev_type != "newMessage":
            return

        sender = payload.get("from", {})
        sender_id = str(sender.get("userId") or "")
        chat = payload.get("chat", {})
        chat_id = str(chat.get("chatId") or sender_id)
        msg_id = str(payload.get("msgId") or "")
        text = str(payload.get("text") or "").strip()

        # Ignore bot's own messages
        if sender_id and sender_id == self._bot_user_id:
            return

        # Check for files / parts
        parts = payload.get("parts", [])
        file_ids = [str(p.get("payload", {}).get("fileId")) for p in parts if p.get("type") == "file"]

        # Thread / quote reference if available
        root_id = ""
        for p in parts:
            if p.get("type") in ("reply", "forward"):
                root_id = str(p.get("payload", {}).get("message", {}).get("msgId") or "")

        channel_event = ChannelEvent(
            provider="vk_teams",
            user_id=sender_id,
            channel_id=chat_id,
            text=text,
            post_id=msg_id,
            file_ids=file_ids,
            root_id=root_id,
        )

        if self._on_message:
            fire_and_forget(self._on_message(channel_event), name="vk-teams-on-message")

    async def send_reply(self, channel_id: str, message: str, root_id: str = "") -> str:
        """Send a reply to a VK Teams chat."""
        try:
            params: Dict[str, Any] = {
                "token": self._token,
                "chatId": channel_id,
                "text": message,
            }
            if root_id:
                params["replyMsgId"] = root_id

            resp = await self._http_client.get(
                f"{self._api_url}/messages/sendText",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error("vk_teams_send_failed", error=data.get("description"), channel=channel_id)
                return ""
            return str(data.get("msgId", ""))
        except Exception as e:
            logger.error("vk_teams_send_error", error=str(e), channel=channel_id)
            return ""

    async def update_reply(self, post_id: str, message: str, channel_id: str = "") -> None:
        """Update an existing message for streaming text in VK Teams."""
        if not post_id or not channel_id:
            return
        try:
            params = {
                "token": self._token,
                "chatId": channel_id,
                "msgId": post_id,
                "text": message,
            }
            resp = await self._http_client.get(
                f"{self._api_url}/messages/editText",
                params=params,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.debug("vk_teams_update_error", error=str(e), post_id=post_id)

    async def send_typing(self, channel_id: str, parent_id: str = "") -> None:
        """Send typing action to VK Teams chat."""
        try:
            params = {
                "token": self._token,
                "chatId": channel_id,
                "style": "typing",
            }
            await self._http_client.get(
                f"{self._api_url}/chats/actionsSend",
                params=params,
            )
        except Exception:
            pass

    async def send_post_with_files(
        self,
        channel_id: str,
        message: str,
        file_ids_or_paths: List[str],
        root_id: str = "",
    ) -> str:
        """Send post with file attachments to VK Teams."""
        text = message
        if file_ids_or_paths:
            file_notes = "\n".join([f"📎 Attachment: {p}" for p in file_ids_or_paths])
            text = f"{text}\n\n{file_notes}" if text else file_notes
        return await self.send_reply(channel_id=channel_id, message=text, root_id=root_id)

    async def get_or_create_dm_channel(self, user_id: str) -> str:
        """In VK Teams, the user's userId is their direct chatId."""
        return user_id
