"""
ClawMux — Bitrix24 Chat Bot Adapter.

Communicates with Bitrix24 REST API for corporate chat bots:
- Sends replies via imbot.message.add
- Updates messages for streaming via imbot.message.update
- Emits typing indicators via imbot.chat.startWriting
"""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from src.services.chat_adapter import BaseChatAdapter, ChannelEvent

logger = structlog.get_logger(__name__)


class BitrixAdapter(BaseChatAdapter):
    """
    Bitrix24 Chat Bot adapter implementing BaseChatAdapter.
    Incoming messages are handled by the /api/v1/bitrix/event webhook.
    """

    def __init__(
        self,
        webhook_url: str,
        bot_id: int = 0,
    ) -> None:
        self._webhook_url = webhook_url.rstrip("/")
        self._bot_id = bot_id
        self._http_client = httpx.AsyncClient(timeout=15.0)
        self._running = False
        self._on_message: Callable[[ChannelEvent], Awaitable[None]] | None = None

    @property
    def name(self) -> str:
        return "bitrix"

    @property
    def is_connected(self) -> bool:
        return self._running and bool(self._webhook_url)

    async def start(
        self,
        on_message: Callable[[ChannelEvent], Awaitable[None]],
    ) -> None:
        """Initialize adapter and bind incoming message handler."""
        self._on_message = on_message
        self._running = True
        logger.info("bitrix_adapter_started", bot_id=self._bot_id)

    async def send_reply(self, channel_id: str, message: str, root_id: str = "") -> str:
        """Send message via Bitrix24 imbot.message.add."""
        url = f"{self._webhook_url}/imbot.message.add.json"
        payload: dict[str, Any] = {
            "DIALOG_ID": channel_id,
            "MESSAGE": message,
        }
        if self._bot_id:
            payload["BOT_ID"] = self._bot_id

        try:
            resp = await self._http_client.post(url, json=payload)
            resp.raise_for_status()
            res = resp.json()
            message_id = str(res.get("result", ""))
            return message_id
        except httpx.HTTPError as e:
            logger.error("bitrix_send_reply_error", channel_id=channel_id, error=str(e))
            raise

    async def update_reply(self, post_id: str, message: str, channel_id: str = "") -> None:
        """Update message via Bitrix24 imbot.message.update."""
        if not post_id:
            return

        url = f"{self._webhook_url}/imbot.message.update.json"
        payload: dict[str, Any] = {
            "MESSAGE_ID": int(post_id) if post_id.isdigit() else post_id,
            "MESSAGE": message,
        }
        if self._bot_id:
            payload["BOT_ID"] = self._bot_id

        try:
            resp = await self._http_client.post(url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("bitrix_update_reply_warning", post_id=post_id, error=str(e))

    async def send_typing(self, channel_id: str, parent_id: str = "") -> None:
        """Start typing indicator in Bitrix24 chat."""
        url = f"{self._webhook_url}/imbot.chat.startWriting.json"
        payload: dict[str, Any] = {"DIALOG_ID": channel_id}
        if self._bot_id:
            payload["BOT_ID"] = self._bot_id

        try:
            await self._http_client.post(url, json=payload)
        except Exception:
            pass

    async def send_post_with_files(
        self,
        channel_id: str,
        message: str,
        file_ids_or_paths: list[str],
        root_id: str = "",
    ) -> str:
        """Send message (file links included in text for Bitrix)."""
        # Append file references to message text
        file_notes = "\n".join([f"📎 File: {f}" for f in file_ids_or_paths])
        full_text = f"{message}\n\n{file_notes}" if message else file_notes
        return await self.send_reply(channel_id, full_text, root_id=root_id)

    async def get_or_create_dm_channel(self, user_id: str) -> str:
        """In Bitrix24, user ID directly addresses the DM dialog."""
        return str(user_id)

    async def stop(self) -> None:
        """Clean up HTTP client."""
        self._running = False
        await self._http_client.aclose()
        logger.info("bitrix_stopped")
