"""
ClawMux — Unified Chat Adapter Interface & Provider Registry.

Defines the abstract contract for multi-channel communication (Mattermost, Telegram, Bitrix24, etc.)
and the registry coordinating active adapters.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Set

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ChannelEvent:
    """Unified incoming message event from any chat provider."""

    user_id: str
    channel_id: str
    text: str
    post_id: str
    file_ids: List[str] = field(default_factory=list)
    provider: str = "mattermost"
    root_id: str = ""

    def __repr__(self) -> str:
        return (
            f"ChannelEvent(provider={self.provider!r}, user_id={self.user_id!r}, "
            f"channel_id={self.channel_id!r}, text={self.text[:50]!r}, "
            f"file_ids={self.file_ids!r}, root_id={self.root_id!r})"
        )


class BaseChatAdapter(ABC):
    """
    Abstract interface that all chat platforms (Mattermost, Telegram, Bitrix24, etc.) must implement.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier, e.g. 'mattermost', 'telegram', 'bitrix'."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the adapter is actively connected and operational."""
        ...

    @abstractmethod
    async def start(self, on_message: Callable[[ChannelEvent], Awaitable[None]]) -> None:
        """Connect to provider and start listening for events."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect and clean up resources."""
        ...

    @abstractmethod
    async def send_reply(self, channel_id: str, message: str, root_id: str = "") -> str:
        """
        Send a text message to a channel or thread.
        Returns the ID of the created message/post.
        """
        ...

    @abstractmethod
    async def update_reply(self, post_id: str, message: str, channel_id: str = "") -> None:
        """Update an existing message (used for streaming)."""
        ...

    @abstractmethod
    async def send_typing(self, channel_id: str, parent_id: str = "") -> None:
        """Display typing indicator to the user."""
        ...

    @abstractmethod
    async def send_post_with_files(
        self,
        channel_id: str,
        message: str,
        file_ids_or_paths: List[str],
        root_id: str = "",
    ) -> str:
        """Send a message with file attachments."""
        ...

    @abstractmethod
    async def get_or_create_dm_channel(self, user_id: str) -> str:
        """Get or open a direct message channel for proactive delivery."""
        ...


class ProviderRegistry:
    """Registry managing active chat platform adapters."""

    def __init__(self) -> None:
        self._adapters: Dict[str, BaseChatAdapter] = {}

    def register(self, adapter: BaseChatAdapter) -> None:
        """Register a chat adapter instance."""
        # In unit tests, adapter might be an AsyncMock where accessing .name produces a coroutine or mock
        raw_name = adapter.__dict__.get("name", getattr(type(adapter), "name", "mattermost"))
        if isinstance(raw_name, property):
            try:
                raw_name = raw_name.fget(adapter)
            except Exception:
                raw_name = "mattermost"
        elif not isinstance(raw_name, str):
            raw_name = "mattermost"
        name = str(raw_name).strip().lower()
        self._adapters[name] = adapter
        logger.info("provider_adapter_registered", provider=name)

    def get(self, name: str) -> Optional[BaseChatAdapter]:
        """Retrieve adapter by name."""
        return self._adapters.get(name.strip().lower())

    def all(self) -> List[BaseChatAdapter]:
        """List all registered adapters."""
        return list(self._adapters.values())

    def supported_providers(self) -> Set[str]:
        """Return all registered provider names."""
        return set(self._adapters.keys())

    def is_registered(self, name: str) -> bool:
        """Check if a provider is registered."""
        return name.strip().lower() in self._adapters
