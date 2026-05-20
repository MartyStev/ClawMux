"""
ClawMux — Router Core.

The central orchestrator:
  1. Receive Mattermost event → resolve user → send to OpenClaw → reply to Mattermost
  2. Receive proactive OpenClaw message → look up last known channel → push to Mattermost

The router stores a user_id → channel_id mapping updated on every incoming message.
This enables delivering proactive OpenClaw messages (reminders, alerts) even when
the user has not sent a message recently.
"""

import asyncio
import httpx
import random
import time
from typing import Dict, Optional

import structlog

from src.core.config import settings
from src.services.dify_client import DifyClient
from src.services.file_manager import FileManager, build_attachment_context, container_path_to_host, extract_uuid_from_instance_url
from src.services.mapping import (
    DEFAULT_PROVIDER,
    MappingStorage,
    InstanceNotFoundError,
    InstanceInfo,
)
from src.services.mattermost import MattermostClient, MattermostEvent
from src.utils.metrics import messages_total, request_duration, ws_errors_total
from src.services.ws_manager import WSConnectionManager

logger = structlog.get_logger(__name__)


class Router:
    """
    Core routing logic: Mattermost event ↔ OpenClaw instance.
    """

    def __init__(
        self,
        mapping: MappingStorage,
        ws_manager: WSConnectionManager,
        mattermost: MattermostClient,
    ):
        self.mapping = mapping
        self.ws_manager = ws_manager
        self.mattermost = mattermost
        self.file_manager = FileManager(http_client=mattermost._http_client)
        # Last known channel per identity key — used for proactive delivery.
        self._user_channels: Dict[str, str] = {}
        # Dify fallback — active only when DIFY_API_KEY is configured
        self._dify: Optional[DifyClient] = (
            DifyClient(
                base_url=settings.dify_base_url,
                api_key=settings.dify_api_key,
                timeout_sec=settings.dify_timeout_sec,
            )
            if settings.dify_api_key
            else None
        )

    @staticmethod
    def _identity_key(provider: str, provider_user_id: str) -> str:
        return f"{provider}:{provider_user_id}"

    @classmethod
    def _normalize_identity_key(cls, provider: str, user_id: str) -> str:
        return user_id if user_id.startswith(f"{provider}:") else cls._identity_key(provider, user_id)

    @staticmethod
    def _provider_user_id(identity_key: str, provider: str) -> str:
        prefix = f"{provider}:"
        return identity_key[len(prefix):] if identity_key.startswith(prefix) else identity_key

    async def handle_event(self, event: MattermostEvent) -> None:
        """
        Process an incoming Mattermost message.

        Flow:
          1. Update user → channel mapping
          2. Look up user's OpenClaw instance
          3. Send message via persistent WS connection
          4. Reply in Mattermost
        """
        identity_key = self._identity_key(event.provider, event.user_id)
        log = logger.bind(
            provider=event.provider,
            user_id=identity_key,
            provider_user_id=event.user_id,
            channel_id=event.channel_id,
            post_id=event.post_id,
        )

        # Always update channel mapping so proactive messages know where to go
        self._user_channels[identity_key] = event.channel_id

        # Use cached InstanceInfo if WS connection already exists — avoids a
        # DB round-trip on every message for connected users.
        info = self.ws_manager.get_cached_info(identity_key)
        if info is None:
            try:
                info = await self.mapping.get_instance_by_identity(
                    event.provider,
                    event.user_id,
                )
                log = log.bind(instance_url=info.instance_url)
            except InstanceNotFoundError:
                log.warning("user_not_mapped")
                messages_total.labels(status="unmapped").inc()
                # ── Dify fallback ──────────────────────────────────────────────
                # If a Dify API key is configured, route the message there
                # instead of showing a hard error to the user.
                if self._dify is not None:
                    await self._handle_dify_fallback(event, log)
                else:
                    await self.mattermost.send_reply(
                        event.channel_id,
                        "⚠️ No OpenClaw instance is assigned to your account. "
                        "Please contact the administrator.",
                    )
                return
        else:
            log = log.bind(instance_url=info.instance_url)

        # ── Route A: download Mattermost attachments → shared volume ──────────────
        # If the user attached files, download them to workspace/downloads/ and
        # append their container-side paths to the message as a system context block.
        message_text = event.text
        if event.file_ids:
            uuid = extract_uuid_from_instance_url(info.instance_url)
            if uuid:
                try:
                    downloaded = await self.file_manager.download_attachments(
                        event.file_ids, uuid
                    )
                    attachment_context = build_attachment_context(downloaded)
                    if attachment_context:
                        if not message_text.strip():
                            message_text = "User attached file(s) without text." + attachment_context
                        else:
                            message_text = message_text + attachment_context
                except (OSError, httpx.HTTPError) as e:
                    log.warning("attachment_download_failed", error=str(e))
            else:
                log.warning("attachment_uuid_not_found", instance_url=info.instance_url)

        # Select a random status phrase to show while generating
        thinking_phrases = [
            "💭 Thinking...",
            "✍️ Writing an answer...",
            "🧠 Processing...",
            "🔍 Reviewing the request...",
            "⚙️ Working...",
            "⏳ One moment...",
            "🤓 Remembering...",
            "📚 Looking for information...",
            "✨ Creating a response...",
            "📝 Formulating a reply...",
            "🤖 Gears are turning...",
            "💡 Gathering ideas...",
            "🧩 Putting the pieces together...",
            "🚀 Preparing the answer...",
            "🧐 Analyzing...",
        ]
        placeholder_text = random.choice(thinking_phrases)
        placeholder_id = ""
        try:
            placeholder_id = await self.mattermost.send_reply(
                event.channel_id, placeholder_text
            )
        except httpx.HTTPError as e:
            log.warning("failed_to_create_placeholder", error=str(e))

        # Start a background task that sends 'typing...' every 4s.
        # Mattermost hides the typing indicator after ~5s, so we repeat it
        # for as long as OpenClaw is generating a response.
        typing_task = asyncio.create_task(
            self._typing_loop(event.channel_id),
            name=f"typing-{identity_key[:32]}",
        )

        _t_start = time.monotonic()

        try:
            log.info("routing_message", session_key=f"mm:chan:{event.channel_id}")
            response, media_paths = await self.ws_manager.send_message(
                user_id=identity_key,
                info=info,
                message=message_text,
                session_key=f"mm:chan:{event.channel_id}",
                on_stream=None
            )

            if response:
                # ── Route B: upload agent-generated files → Mattermost ───────────
                # If the agent set mediaUrls in its reply, upload those files
                # from shared volume and attach them to the Mattermost post.
                uploaded_file_ids: list[str] = []
                if media_paths:
                    uuid = extract_uuid_from_instance_url(info.instance_url)
                    if uuid:
                        for container_path in media_paths:
                            host_path = container_path_to_host(container_path, uuid)
                            if host_path:
                                fid = await self.file_manager.upload_to_mattermost(
                                    host_path, event.channel_id
                                )
                                if fid:
                                    uploaded_file_ids.append(fid)

                if uploaded_file_ids:
                    # Send/update post with file attachments
                    if placeholder_id:
                        # Replace placeholder with a new post carrying files
                        # (Mattermost API doesn't support adding files to existing posts)
                        await self.mattermost.update_reply(placeholder_id, response)
                        await self.mattermost.send_post_with_files(
                            channel_id=event.channel_id,
                            message="",
                            file_ids=uploaded_file_ids,
                        )
                    else:
                        await self.mattermost.send_post_with_files(
                            channel_id=event.channel_id,
                            message=response,
                            file_ids=uploaded_file_ids,
                        )
                    log.info(
                        "response_delivered_with_files",
                        response_len=len(response),
                        file_ids=uploaded_file_ids,
                    )
                else:
                    if placeholder_id:
                        await self.mattermost.update_reply(placeholder_id, response)
                    else:
                        await self.mattermost.send_reply(event.channel_id, response)
                    log.info("response_delivered", response_len=len(response))

                messages_total.labels(status="success").inc()
            else:
                log.warning("empty_response_from_openclaw")
                messages_total.labels(status="empty").inc()

        except Exception as e:
            log.error("routing_error", error=str(e))
            messages_total.labels(status="error").inc()
            ws_errors_total.labels(error_type="routing_error").inc()
            await self.mattermost.send_reply(
                event.channel_id,
                "❌ An error occurred while processing the request. Please try again later.",
            )
        finally:
            # Always stop the typing loop once we have a response (or error)
            typing_task.cancel()
            request_duration.observe(time.monotonic() - _t_start)

    async def _typing_loop(self, channel_id: str) -> None:
        """Send 'typing...' every 4s until cancelled."""
        try:
            while True:
                await self.mattermost.send_typing(channel_id)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    async def _handle_dify_fallback(self, event: MattermostEvent, log) -> None:
        """
        Route a message to Dify when the user has no OpenClaw instance.

        Mirrors the main handle_event flow:
          1. Post a placeholder "thinking…" message
          2. Run a typing loop in the background
          3. Call Dify (streaming, aggregated)
          4. Replace the placeholder with the final answer
        """
        log.info("dify_fallback_routing")
        identity_key = self._identity_key(event.provider, event.user_id)

        thinking_phrases = [
            "💭 Thinking...",
            "✍️ Writing an answer...",
            "🧠 Processing...",
            "🔍 Reviewing the request...",
            "⚙️ Working...",
            "⏳ One moment...",
            "📚 Looking for information...",
            "🚀 Preparing the answer...",
        ]
        placeholder_text = random.choice(thinking_phrases)
        placeholder_id = ""
        try:
            placeholder_id = await self.mattermost.send_reply(
                event.channel_id, placeholder_text
            )
        except httpx.HTTPError as e:
            log.warning("dify_fallback_placeholder_failed", error=str(e))

        typing_task = asyncio.create_task(
            self._typing_loop(event.channel_id),
            name=f"typing-dify-{identity_key[:32]}",
        )

        _t_start = time.monotonic()
        try:
            response = await self._dify.chat(  # type: ignore[union-attr]
                user_id=event.user_id,
                message=event.text,
            )

            if response:
                if placeholder_id:
                    await self.mattermost.update_reply(placeholder_id, response)
                else:
                    await self.mattermost.send_reply(event.channel_id, response)
                log.info("dify_fallback_delivered", response_len=len(response))
                messages_total.labels(status="dify_fallback").inc()
            else:
                log.warning("dify_fallback_empty_response")
                err_text = "❌ Unable to get a response. Please try again later."
                if placeholder_id:
                    await self.mattermost.update_reply(placeholder_id, err_text)
                else:
                    await self.mattermost.send_reply(event.channel_id, err_text)
                messages_total.labels(status="dify_fallback_empty").inc()

        except Exception as e:
            log.error("dify_fallback_error", error=str(e))
            messages_total.labels(status="dify_fallback_error").inc()
            err_text = "❌ An error occurred while processing the request. Please try again later."
            try:
                if placeholder_id:
                    await self.mattermost.update_reply(placeholder_id, err_text)
                else:
                    await self.mattermost.send_reply(event.channel_id, err_text)
            except Exception:
                pass
        finally:
            typing_task.cancel()
            request_duration.observe(time.monotonic() - _t_start)

    async def get_or_create_channel(
        self,
        identity_key: str,
        provider_user_id: str,
        provider: str = DEFAULT_PROVIDER,
    ) -> str:
        """Get the user's last known channel, or create a DM channel."""
        channel_id = self._user_channels.get(identity_key)
        if not channel_id:
            if provider != DEFAULT_PROVIDER:
                logger.warning("provider_delivery_not_supported", provider=provider)
                return ""
            channel_id = await self.mattermost.get_or_create_dm_channel(provider_user_id)
            if channel_id:
                self._user_channels[identity_key] = channel_id
        return channel_id or ""

    async def trigger_message(
        self,
        user_id: str,
        info: InstanceInfo,
        text: str,
        session_key: Optional[str] = None,
        provider: str = DEFAULT_PROVIDER,
    ) -> None:
        """
        Handle a message triggered by the Control-Plane API.
        Similar to handle_event, but the message originates from an external system.
        Does NOT show a typing indicator or streaming, wait for full response
        and then deliver it as a single proactive message.
        """
        identity_key = self._identity_key(provider, user_id)
        log = logger.bind(provider=provider, user_id=identity_key, provider_user_id=user_id)
        channel_id = await self.get_or_create_channel(identity_key, user_id, provider)

        if not session_key:
            session_key = f"mm:chan:{channel_id}" if channel_id else "agent:main:main"

        log = log.bind(channel_id=channel_id, session_key=session_key)

        _t_start = time.monotonic()
        try:
            log.info("triggering_message")
            response, media_paths = await self.ws_manager.send_message(
                user_id=identity_key,
                info=info,
                message=text,
                session_key=session_key,
                on_stream=None
            )

            if response:
                if channel_id:
                    await self.mattermost.send_reply(channel_id, response)
                log.info("trigger_response_delivered", response_len=len(response))
            else:
                log.warning("empty_response_from_openclaw_trigger")

        except Exception as e:
            log.error("trigger_routing_error", error=str(e))
            if channel_id:
                await self.mattermost.send_reply(
                    channel_id,
                    "❌ An error occurred while executing the background task.",
                )
        finally:
            request_duration.observe(time.monotonic() - _t_start)


    async def handle_proactive(
        self,
        user_id: str,
        text: str,
        provider: str = DEFAULT_PROVIDER,
    ) -> None:
        """
        Deliver a proactive message from OpenClaw to the user's last known channel.
        Called by WSConnectionManager when OpenClaw pushes an unsolicited message.
        """
        identity_key = self._normalize_identity_key(provider, user_id)
        provider_user_id = self._provider_user_id(identity_key, provider)
        log = logger.bind(
            provider=provider,
            user_id=identity_key,
            provider_user_id=provider_user_id,
        )

        channel_id = await self.get_or_create_channel(
            identity_key,
            provider_user_id,
            provider,
        )
        if not channel_id:
            log.warning("proactive_no_channel_known_and_dm_failed", text_preview=text[:80])
            return

        if not text:
            log.warning("proactive_empty_text")
            return

        try:
            await self.mattermost.send_reply(channel_id, text)
            log.info("proactive_delivered", channel_id=channel_id, text_len=len(text))
        except Exception as e:
            log.error("proactive_delivery_error", error=str(e))
