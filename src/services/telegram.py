"""
ClawMux — Telegram Channel Adapter.

Connects to Telegram Bot API using httpx to:
- Poll for incoming messages / topics via getUpdates (Long Polling)
- Route incoming messages to OpenClaw instances
- Send replies, edit streaming answers, and send typing indicators
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from src.services.chat_adapter import BaseChatAdapter, ChannelEvent
from src.utils.tasks import fire_and_forget

logger = structlog.get_logger(__name__)


class TelegramAdapter(BaseChatAdapter):
    """
    Telegram Bot API adapter implementing BaseChatAdapter.
    """

    def __init__(
        self,
        bot_token: str,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self._token = bot_token
        self._api_url = f"{base_url.rstrip('/')}/bot{bot_token}"
        self._http_client = httpx.AsyncClient(timeout=35.0)
        self._running = False
        self._listen_task: asyncio.Task | None = None
        self._last_update_id = 0
        self._bot_id = ""
        self._on_message: Callable[[ChannelEvent], Awaitable[None]] | None = None

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def is_connected(self) -> bool:
        return self._running and bool(self._bot_id)

    def _redact(self, text: str) -> str:
        """Strip the bot token from any text before it reaches the logs.

        httpx exception strings embed the full request URL, which contains
        the token (/bot<token>/method).
        """
        return text.replace(self._token, "***") if self._token else text

    def _check(self, resp: httpx.Response) -> None:
        """raise_for_status with a token-redacted error message."""
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise httpx.HTTPError(self._redact(str(e))) from None

    async def start(
        self,
        on_message: Callable[[ChannelEvent], Awaitable[None]],
    ) -> None:
        """Connect to Telegram Bot API and start long polling for updates."""
        self._on_message = on_message
        self._running = True

        # Fetch bot user info to verify token
        try:
            resp = await self._http_client.get(f"{self._api_url}/getMe")
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram getMe failed: {data.get('description')}")
            me = data.get("result", {})
            self._bot_id = str(me.get("id", ""))
            logger.info(
                "telegram_logged_in",
                bot_id=self._bot_id,
                username=me.get("username"),
            )
        except Exception as e:
            logger.error("telegram_login_failed", error=self._redact(str(e)))
            raise

        self._listen_task = asyncio.create_task(
            self._poll_loop(),
            name="telegram-polling",
        )

    async def _poll_loop(self) -> None:
        """Continuously long-poll for updates from Telegram."""
        while self._running:
            try:
                params: dict[str, Any] = {
                    "offset": self._last_update_id + 1,
                    "timeout": 20,
                    "allowed_updates": ["message", "channel_post"],
                }
                resp = await self._http_client.get(
                    f"{self._api_url}/getUpdates",
                    params=params,
                    timeout=30.0,
                )
                if resp.status_code != 200:
                    logger.warning("telegram_poll_error", status_code=resp.status_code)
                    await asyncio.sleep(3)
                    continue

                data = resp.json()
                if not data.get("ok"):
                    await asyncio.sleep(3)
                    continue

                updates = data.get("result", [])
                for update in updates:
                    self._last_update_id = max(self._last_update_id, update.get("update_id", 0))
                    await self._handle_update(update)

            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                logger.error("telegram_poll_exception", error=self._redact(str(e)))
                await asyncio.sleep(5)

    async def _handle_update(self, update: dict) -> None:
        """Parse incoming Telegram update and dispatch ChannelEvent."""
        message = update.get("message") or update.get("channel_post")
        if not message:
            return

        from_user = message.get("from", {})
        chat = message.get("chat", {})

        user_id = str(from_user.get("id", ""))
        channel_id = str(chat.get("id", ""))
        text = message.get("text", "") or message.get("caption", "")
        post_id = str(message.get("message_id", ""))

        # Topic support: message_thread_id
        root_id = ""
        if message.get("message_thread_id"):
            root_id = str(message.get("message_thread_id"))

        # In Telegram channel posts, from_user might be missing; fallback to chat.id
        if not user_id and channel_id:
            user_id = channel_id

        # File attachments (documents / photos)
        file_ids = []
        if message.get("document"):
            file_ids.append(message["document"]["file_id"])
        elif message.get("photo"):
            # Pick highest resolution photo
            file_ids.append(message["photo"][-1]["file_id"])

        if not text and not file_ids:
            return

        event = ChannelEvent(
            provider="telegram",
            user_id=user_id,
            channel_id=channel_id,
            text=text,
            post_id=post_id,
            file_ids=file_ids,
            root_id=root_id,
        )

        if self._on_message:

            async def _dispatch():
                try:
                    await self._on_message(event)
                except Exception as ex:
                    logger.error("telegram_on_message_error", error=self._redact(str(ex)))

            fire_and_forget(_dispatch(), name="telegram-on-message")

    async def send_reply(self, channel_id: str, message: str, root_id: str = "") -> str:
        """Send message to a chat or topic."""
        body: dict[str, Any] = {
            "chat_id": channel_id,
            "text": message,
        }
        if root_id and root_id.isdigit():
            body["message_thread_id"] = int(root_id)

        try:
            resp = await self._http_client.post(f"{self._api_url}/sendMessage", json=body)
            self._check(resp)
            data = resp.json()
            post_id = str(data.get("result", {}).get("message_id", ""))
            return post_id
        except httpx.HTTPError as e:
            logger.error("telegram_send_reply_error", channel_id=channel_id, error=self._redact(str(e)))
            # Re-raise redacted: the original message embeds the bot-token URL,
            # and upstream handlers log str(exception) too.
            raise httpx.HTTPError(self._redact(str(e))) from None

    async def update_reply(self, post_id: str, message: str, channel_id: str = "") -> None:
        """Edit an existing message for streaming updates."""
        if not post_id or not channel_id:
            return

        body = {
            "chat_id": channel_id,
            "message_id": int(post_id) if post_id.isdigit() else post_id,
            "text": message,
        }
        try:
            resp = await self._http_client.post(f"{self._api_url}/editMessageText", json=body)
            # 400 with "message is not modified" is normal in Telegram streaming
            if resp.status_code not in (200, 400):
                resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("telegram_update_reply_warning", post_id=post_id, error=self._redact(str(e)))

    async def send_typing(self, channel_id: str, parent_id: str = "") -> None:
        """Send chat action typing indicator."""
        body: dict[str, Any] = {
            "chat_id": channel_id,
            "action": "typing",
        }
        if parent_id and parent_id.isdigit():
            body["message_thread_id"] = int(parent_id)

        try:
            await self._http_client.post(f"{self._api_url}/sendChatAction", json=body)
        except Exception as e:
            logger.warning("telegram_typing_error", error=self._redact(str(e)))

    async def send_post_with_files(
        self,
        channel_id: str,
        message: str,
        file_ids_or_paths: list[str],
        root_id: str = "",
    ) -> str:
        """Send document files with caption to Telegram chat."""
        last_id = ""
        for item in file_ids_or_paths:
            if os.path.isfile(item):
                filename = os.path.basename(item)
                with open(item, "rb") as fh:
                    content = fh.read()
                files = {"document": (filename, content)}
                data: dict[str, Any] = {"chat_id": channel_id, "caption": message}
                if root_id and root_id.isdigit():
                    data["message_thread_id"] = int(root_id)
                resp = await self._http_client.post(
                    f"{self._api_url}/sendDocument",
                    data=data,
                    files=files,
                )
                self._check(resp)
                res = resp.json()
                last_id = str(res.get("result", {}).get("message_id", ""))
                # Only put caption on first document
                message = ""
            else:
                # If file_id, send via existing sendDocument file_id
                body: dict[str, Any] = {
                    "chat_id": channel_id,
                    "document": item,
                    "caption": message,
                }
                if root_id and root_id.isdigit():
                    body["message_thread_id"] = int(root_id)
                resp = await self._http_client.post(f"{self._api_url}/sendDocument", json=body)
                self._check(resp)
                res = resp.json()
                last_id = str(res.get("result", {}).get("message_id", ""))
                message = ""

        return last_id

    async def get_or_create_dm_channel(self, user_id: str) -> str:
        """In Telegram, direct message chat ID is identical to the user's telegram user_id."""
        return str(user_id)

    async def stop(self) -> None:
        """Stop long-polling and close HTTP client."""
        self._running = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        await self._http_client.aclose()
        logger.info("telegram_stopped")
