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
from typing import Any, Dict, Optional

import structlog

from src.core.config import settings
from src.services.chat_adapter import BaseChatAdapter, ChannelEvent, ProviderRegistry
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


from src.services.provisioner import InstanceProvisioner, ProvisioningError


class StreamUpdater:
    """
    Throttles streaming updates to chat platform APIs to avoid rate limits.
    """

    def __init__(self, adapter: Any, post_id: str, channel_id: str = "", interval: float = 0.8):
        self.adapter = adapter
        self.post_id = post_id
        self.channel_id = channel_id
        self.interval = interval
        self._last_update = 0.0
        self._last_text = ""
        self._lock = asyncio.Lock()

    async def on_stream(self, text: str) -> None:
        if not self.post_id or not text.strip():
            return
        now = time.monotonic()
        if now - self._last_update < self.interval:
            self._last_text = text
            return

        async with self._lock:
            if now - self._last_update < self.interval:
                self._last_text = text
                return
            self._last_update = now
            self._last_text = text
            try:
                if self.channel_id:
                    await self.adapter.update_reply(self.post_id, text, channel_id=self.channel_id)
                else:
                    await self.adapter.update_reply(self.post_id, text)
            except Exception:
                pass


class Router:
    """
    Core multi-channel routing logic: Chat event ↔ OpenClaw instance.
    """

    def __init__(
        self,
        mapping: MappingStorage,
        ws_manager: WSConnectionManager,
        mattermost: Optional[Any] = None,
        providers: Optional[ProviderRegistry] = None,
    ):
        self.mapping = mapping
        self.ws_manager = ws_manager

        if providers is not None:
            self.providers = providers
        else:
            self.providers = ProviderRegistry()

        if mattermost is not None:
            self.mattermost = mattermost
            if not self.providers.is_registered("mattermost"):
                self.providers.register(mattermost)
        else:
            self.mattermost = self.providers.get("mattermost")

        mm_http = getattr(self.mattermost, "_http_client", None)
        self.file_manager = FileManager(http_client=mm_http) if mm_http else None

        self.provisioner = InstanceProvisioner(mapping=self.mapping)
        # Fast-path cache for last known channel per identity key — the source
        # of truth is the user_channel table (survives restarts, shared across
        # replicas), this dict only avoids a DB read on the proactive path.
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

    def get_adapter(self, provider: str) -> Optional[BaseChatAdapter]:
        adapter = self.providers.get(provider)
        if adapter is None:
            if provider == DEFAULT_PROVIDER or not provider:
                return self.mattermost
        return adapter

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

    async def handle_event(self, event: ChannelEvent) -> None:
        """
        Process an incoming chat message from any supported provider.

        Flow:
          1. Update user → channel mapping
          2. Look up user's OpenClaw instance
          3. Send message via persistent WS connection
          4. Reply via corresponding channel adapter
        """
        identity_key = self._identity_key(event.provider, event.user_id)
        root_id = getattr(event, "root_id", "")
        adapter = self.get_adapter(event.provider)
        if not adapter:
            logger.error("no_adapter_for_provider", provider=event.provider)
            return

        log = logger.bind(
            provider=event.provider,
            user_id=identity_key,
            provider_user_id=event.user_id,
            channel_id=event.channel_id,
            post_id=event.post_id,
            root_id=root_id,
        )

        # Always update channel mapping so proactive messages know where to go
        await self._remember_channel(
            identity_key, event.provider, event.user_id, event.channel_id
        )

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
                # ── Auto-Provisioning fallback ────────────────────────────────
                if settings.enable_auto_provisioning:
                    try:
                        placeholder_id = await adapter.send_reply(
                            event.channel_id,
                            "⏳ Initializing your personal OpenClaw workspace...",
                            root_id=root_id,
                        )
                        info = await self.provisioner.provision_instance(
                            provider=event.provider,
                            user_id=event.user_id,
                        )
                        log = log.bind(instance_url=info.instance_url)
                        log.info("auto_provisioning_success")
                        if placeholder_id:
                            if event.provider == "mattermost":
                                await adapter.update_reply(placeholder_id, "✅ Workspace ready! Processing your message...")
                            else:
                                await adapter.update_reply(placeholder_id, "✅ Workspace ready! Processing your message...", channel_id=event.channel_id)
                    except Exception as e:
                        log.error("auto_provisioning_failed", error=str(e))
                        messages_total.labels(status="provisioning_failed").inc()
                        if self._dify is not None:
                            await self._handle_dify_fallback(event, log)
                        else:
                            await adapter.send_reply(
                                event.channel_id,
                                f"❌ Failed to initialize your workspace: {e}",
                                root_id=root_id,
                            )
                        return
                elif self._dify is not None:
                    messages_total.labels(status="unmapped").inc()
                    await self._handle_dify_fallback(event, log)
                    return
                else:
                    messages_total.labels(status="unmapped").inc()
                    await adapter.send_reply(
                        event.channel_id,
                        "⚠️ No OpenClaw instance is assigned to your account. "
                        "Please contact the administrator.",
                        root_id=root_id,
                    )
                    return
        else:
            log = log.bind(instance_url=info.instance_url)

        # ── Route A: download attachments → shared volume ──────────────
        message_text = event.text
        if event.file_ids and self.file_manager:
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
            placeholder_id = await adapter.send_reply(
                event.channel_id, placeholder_text, root_id=root_id
            )
        except Exception as e:
            log.warning("failed_to_create_placeholder", error=str(e))

        typing_task = asyncio.create_task(
            self._typing_loop(adapter, event.channel_id, parent_id=root_id),
            name=f"typing-{identity_key[:32]}",
        )

        streamer = StreamUpdater(adapter, placeholder_id, channel_id=event.channel_id) if placeholder_id else None
        _t_start = time.monotonic()

        try:
            log.info("routing_message", session_key=f"{event.provider}:chan:{event.channel_id}")
            response, media_paths = await self.ws_manager.send_message(
                user_id=identity_key,
                info=info,
                message=message_text,
                session_key=f"{event.provider}:chan:{event.channel_id}",
                on_stream=streamer.on_stream if streamer else None,
            )

            if response:
                uploaded_file_ids: list[str] = []
                if media_paths:
                    uuid = extract_uuid_from_instance_url(info.instance_url)
                    if uuid:
                        for container_path in media_paths:
                            host_path = container_path_to_host(container_path, uuid)
                            if host_path:
                                if event.provider == "mattermost" and self.file_manager:
                                    fid = await self.file_manager.upload_to_mattermost(
                                        host_path, event.channel_id
                                    )
                                    if fid:
                                        uploaded_file_ids.append(fid)
                                else:
                                    uploaded_file_ids.append(host_path)

                if uploaded_file_ids:
                    if placeholder_id:
                        if event.provider == "mattermost":
                            await adapter.update_reply(placeholder_id, response)
                        else:
                            await adapter.update_reply(placeholder_id, response, channel_id=event.channel_id)
                        await adapter.send_post_with_files(
                            channel_id=event.channel_id,
                            message="",
                            file_ids_or_paths=uploaded_file_ids,
                            root_id=root_id,
                        )
                    else:
                        await adapter.send_post_with_files(
                            channel_id=event.channel_id,
                            message=response,
                            file_ids_or_paths=uploaded_file_ids,
                            root_id=root_id,
                        )
                    log.info(
                        "response_delivered_with_files",
                        response_len=len(response),
                        file_ids=uploaded_file_ids,
                    )
                else:
                    if placeholder_id:
                        if event.provider == "mattermost":
                            await adapter.update_reply(placeholder_id, response)
                        else:
                            await adapter.update_reply(placeholder_id, response, channel_id=event.channel_id)
                    else:
                        await adapter.send_reply(event.channel_id, response, root_id=root_id)
                    log.info("response_delivered", response_len=len(response))

                messages_total.labels(status="success").inc()
            else:
                log.warning("empty_response_from_openclaw")
                messages_total.labels(status="empty").inc()

        except Exception as e:
            log.error("routing_error", error=str(e))
            messages_total.labels(status="error").inc()
            ws_errors_total.labels(error_type="routing_error").inc()
            await adapter.send_reply(
                event.channel_id,
                "❌ An error occurred while processing the request. Please try again later.",
                root_id=root_id,
            )
        finally:
            typing_task.cancel()
            request_duration.observe(time.monotonic() - _t_start)

    async def _remember_channel(
        self, identity_key: str, provider: str, provider_user_id: str, channel_id: str
    ) -> None:
        """Cache the last known channel locally and persist it to the DB.

        A DB failure must never break message routing, so it is only logged.
        """
        self._user_channels[identity_key] = channel_id
        try:
            await self.mapping.remember_channel(provider, provider_user_id, channel_id)
        except Exception as e:
            logger.warning(
                "channel_persist_failed",
                provider=provider,
                user_id=identity_key,
                error=str(e),
            )

    async def _typing_loop(self, adapter: Any, channel_id: str, parent_id: str = "") -> None:
        """Send 'typing...' every 4s until cancelled."""
        try:
            while True:
                if hasattr(adapter, "send_typing"):
                    await adapter.send_typing(channel_id, parent_id=parent_id)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    async def _handle_dify_fallback(
        self,
        event: ChannelEvent,
        log,
        adapter: Optional[Any] = None,
    ) -> None:
        """
        Route a message to Dify when the user has no OpenClaw instance.
        """
        if adapter is None:
            adapter = self.get_adapter(event.provider)
        if not adapter:
            log.error("no_adapter_for_dify_fallback", provider=event.provider)
            return

        log.info("dify_fallback_routing")
        identity_key = self._identity_key(event.provider, event.user_id)
        root_id = getattr(event, "root_id", "")

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
            placeholder_id = await adapter.send_reply(
                event.channel_id, placeholder_text, root_id=root_id
            )
        except Exception as e:
            log.warning("dify_fallback_placeholder_failed", error=str(e))

        typing_task = asyncio.create_task(
            self._typing_loop(adapter, event.channel_id, parent_id=root_id),
            name=f"typing-dify-{identity_key[:32]}",
        )

        streamer = StreamUpdater(adapter, placeholder_id, channel_id=event.channel_id) if placeholder_id else None
        _t_start = time.monotonic()
        try:
            response = await self._dify.chat(  # type: ignore[union-attr]
                user_id=event.user_id,
                message=event.text,
                on_stream=streamer.on_stream if streamer else None,
            )

            if response:
                if placeholder_id:
                    await adapter.update_reply(placeholder_id, response, channel_id=event.channel_id)
                else:
                    await adapter.send_reply(event.channel_id, response, root_id=root_id)
                log.info("dify_fallback_delivered", response_len=len(response))
                messages_total.labels(status="dify_fallback").inc()
            else:
                log.warning("dify_fallback_empty_response")
                err_text = "❌ Unable to get a response. Please try again later."
                if placeholder_id:
                    await adapter.update_reply(placeholder_id, err_text, channel_id=event.channel_id)
                else:
                    await adapter.send_reply(event.channel_id, err_text, root_id=root_id)
                messages_total.labels(status="dify_fallback_empty").inc()

        except Exception as e:
            log.error("dify_fallback_error", error=str(e))
            messages_total.labels(status="dify_fallback_error").inc()
            err_text = "❌ An error occurred while processing the request. Please try again later."
            try:
                if placeholder_id:
                    await adapter.update_reply(placeholder_id, err_text, channel_id=event.channel_id)
                else:
                    await adapter.send_reply(event.channel_id, err_text, root_id=root_id)
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
        """Get the user's last known channel (local cache → DB), or create a DM."""
        channel_id = self._user_channels.get(identity_key)
        if not channel_id:
            try:
                channel_id = await self.mapping.get_channel(provider, provider_user_id)
            except Exception as e:
                logger.warning(
                    "channel_lookup_failed",
                    provider=provider,
                    user_id=identity_key,
                    error=str(e),
                )
            if channel_id:
                self._user_channels[identity_key] = channel_id
                return channel_id

        adapter = self.get_adapter(provider)
        if not adapter:
            logger.warning("provider_delivery_not_supported", provider=provider)
            return ""
        channel_id = await adapter.get_or_create_dm_channel(provider_user_id)
        if channel_id:
            await self._remember_channel(identity_key, provider, provider_user_id, channel_id)
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
        """
        identity_key = self._identity_key(provider, user_id)
        log = logger.bind(provider=provider, user_id=identity_key, provider_user_id=user_id)
        channel_id = await self.get_or_create_channel(identity_key, user_id, provider)
        adapter = self.get_adapter(provider)

        if not session_key:
            session_key = f"{provider}:chan:{channel_id}" if channel_id else "agent:main:main"

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
                if channel_id and adapter:
                    await adapter.send_reply(channel_id, response)
                log.info("trigger_response_delivered", response_len=len(response))
            else:
                log.warning("empty_response_from_openclaw_trigger")

        except Exception as e:
            log.error("trigger_routing_error", error=str(e))
            if channel_id and adapter:
                await adapter.send_reply(
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
        """
        identity_key = self._normalize_identity_key(provider, user_id)
        provider_user_id = self._provider_user_id(identity_key, provider)
        log = logger.bind(
            provider=provider,
            user_id=identity_key,
            provider_user_id=provider_user_id,
        )

        adapter = self.get_adapter(provider)
        if not adapter:
            log.warning("proactive_no_adapter", provider=provider)
            return

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
            await adapter.send_reply(channel_id, text)
            log.info("proactive_delivered", channel_id=channel_id, text_len=len(text))
        except Exception as e:
            log.error("proactive_delivery_error", error=str(e))
