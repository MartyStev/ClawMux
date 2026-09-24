"""
ClawMux — Microsoft Teams Channel Adapter.

Connects to Microsoft Bot Framework / Azure Bot Service REST API to:
- Obtain OAuth2 bearer tokens via login.microsoftonline.com
- Send activities (replies) to conversations
- Update existing activities (for streaming answers)
- Send typing activities
"""

import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
import structlog

from src.services.chat_adapter import BaseChatAdapter, ChannelEvent

logger = structlog.get_logger(__name__)

# Bound for the conversation → serviceUrl map; long-lived bots see a steady
# stream of new conversation ids, so evict least recently used entries.
_MAX_SERVICE_URLS = 10000


class TeamsAdapter(BaseChatAdapter):
    """
    Microsoft Teams Bot adapter implementing BaseChatAdapter.
    """

    def __init__(
        self,
        app_id: str,
        app_password: str,
        oauth_url: str = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token",
    ) -> None:
        self._app_id = app_id.strip()
        self._app_password = app_password.strip()
        self._oauth_url = oauth_url
        self._http_client = httpx.AsyncClient(timeout=30.0)
        self._access_token = ""
        self._token_expires_at = 0.0
        self._running = False
        self._service_urls: "OrderedDict[str, str]" = OrderedDict()  # conversation_id -> serviceUrl (LRU-bounded)
        self._on_message: Optional[Callable[[ChannelEvent], Awaitable[None]]] = None

    @property
    def name(self) -> str:
        return "teams"

    @property
    def is_connected(self) -> bool:
        return self._running and bool(self._app_id)

    async def start(
        self,
        on_message: Callable[[ChannelEvent], Awaitable[None]],
    ) -> None:
        """Initialize adapter and pre-warm OAuth token."""
        self._on_message = on_message
        self._running = True

        if self._app_id and self._app_password:
            try:
                await self._get_access_token()
                logger.info("teams_adapter_authenticated", app_id=self._app_id)
            except Exception as e:
                logger.warning("teams_auth_warning", error=str(e))

    async def stop(self) -> None:
        """Stop adapter and close HTTP client."""
        self._running = False
        await self._http_client.aclose()
        logger.info("teams_adapter_stopped")

    async def _get_access_token(self) -> str:
        """Fetch or return cached OAuth2 client credentials token."""
        now = time.monotonic()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        data = {
            "grant_type": "client_credentials",
            "client_id": self._app_id,
            "client_secret": self._app_password,
            "scope": "https://api.botframework.com/.default",
        }
        resp = await self._http_client.post(self._oauth_url, data=data)
        resp.raise_for_status()
        token_info = resp.json()
        self._access_token = token_info.get("access_token", "")
        expires_in = int(token_info.get("expires_in", 3600))
        self._token_expires_at = now + expires_in
        return self._access_token

    def register_service_url(self, conversation_id: str, service_url: str) -> None:
        """Record the serviceUrl received in the incoming activity.

        Only whitelisted HTTPS Bot Framework hosts are accepted — this value
        becomes the base URL for outbound calls carrying our OAuth Bearer token.
        """
        from src.services.teams_auth import is_allowed_service_url

        if not is_allowed_service_url(service_url):
            logger.error(
                "teams_service_url_not_whitelisted",
                conversation_id=conversation_id,
                service_url=service_url[:120],
            )
            return
        self._service_urls[conversation_id] = service_url.rstrip("/")
        self._service_urls.move_to_end(conversation_id)
        while len(self._service_urls) > _MAX_SERVICE_URLS:
            self._service_urls.popitem(last=False)

    def get_service_url(self, conversation_id: str) -> str:
        url = self._service_urls.get(conversation_id)
        if url is None:
            return "https://smba.trafficmanager.net/teams"
        self._service_urls.move_to_end(conversation_id)
        return url

    async def send_reply(self, channel_id: str, message: str, root_id: str = "") -> str:
        """Send a reply activity to a Teams conversation."""
        try:
            token = await self._get_access_token()
            service_url = self.get_service_url(channel_id)
            url = f"{service_url}/v3/conversations/{channel_id}/activities"

            activity: Dict[str, Any] = {
                "type": "message",
                "text": message,
            }
            if root_id:
                activity["replyToId"] = root_id

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            resp = await self._http_client.post(url, json=activity, headers=headers)
            resp.raise_for_status()
            res_data = resp.json()
            return str(res_data.get("id") or "")
        except Exception as e:
            logger.error("teams_send_reply_error", error=str(e), conversation_id=channel_id)
            return ""

    async def update_reply(self, post_id: str, message: str, channel_id: str = "") -> None:
        """Update an existing activity for streaming text."""
        if not post_id or not channel_id:
            return
        try:
            token = await self._get_access_token()
            service_url = self.get_service_url(channel_id)
            url = f"{service_url}/v3/conversations/{channel_id}/activities/{post_id}"

            activity = {
                "type": "message",
                "text": message,
            }
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            resp = await self._http_client.put(url, json=activity, headers=headers)
            resp.raise_for_status()
        except Exception as e:
            logger.debug("teams_update_reply_error", error=str(e), activity_id=post_id)

    async def send_typing(self, channel_id: str, parent_id: str = "") -> None:
        """Send a typing activity."""
        try:
            token = await self._get_access_token()
            service_url = self.get_service_url(channel_id)
            url = f"{service_url}/v3/conversations/{channel_id}/activities"
            activity = {"type": "typing"}
            headers = {"Authorization": f"Bearer {token}"}
            await self._http_client.post(url, json=activity, headers=headers)
        except Exception:
            pass

    async def send_post_with_files(
        self,
        channel_id: str,
        message: str,
        file_ids_or_paths: List[str],
        root_id: str = "",
    ) -> str:
        """Send message with attachments to Teams."""
        text = message
        if file_ids_or_paths:
            file_notes = "\n".join([f"📎 Attachment: {p}" for p in file_ids_or_paths])
            text = f"{text}\n\n{file_notes}" if text else file_notes
        return await self.send_reply(channel_id=channel_id, message=text, root_id=root_id)

    async def get_or_create_dm_channel(self, user_id: str) -> str:
        return user_id
