"""
ClawMux — Mapping Storage.

Provides lookup:
  provider_user_id + provider → (instance_url, device_credentials)
  external_user_id + provider → provider_user_id + instance info

Reads from tables: instance, app_user, user_identity, user_instance (join).
"""

import structlog
from asyncache import cached
from cachetools import TTLCache
from dataclasses import dataclass
from sqlalchemy import select

from src.core.database import async_session_factory
from src.core.models import AppUser, Instance, UserIdentity, UserInstance

logger = structlog.get_logger(__name__)
DEFAULT_PROVIDER = "mattermost"
SUPPORTED_PROVIDERS = {DEFAULT_PROVIDER}

_identity_cache = TTLCache(maxsize=1000, ttl=600)
_external_id_cache = TTLCache(maxsize=1000, ttl=600)


class InstanceNotFoundError(Exception):
    """Raised when no OpenClaw instance is mapped for a user."""

    def __init__(self, identifier: str):
        self.identifier = identifier
        super().__init__(f"No OpenClaw instance found for {identifier!r}")


class UnsupportedProviderError(Exception):
    """Raised when a provider is not yet supported by routing logic."""

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(f"Unsupported provider: {provider!r}")


@dataclass(slots=True, frozen=True)
class DeviceCredentials:
    """Credentials needed to authenticate with an OpenClaw instance."""

    device_id: str
    public_key_b64: str
    private_key_b64: str
    device_token: str
    gateway_token: str


@dataclass(slots=True, frozen=True)
class InstanceInfo:
    """Result from mapping lookup: URL + credentials."""

    instance_url: str
    credentials: DeviceCredentials


class MappingStorage:
    """Reads user → instance mapping from PostgreSQL (3NF schema)."""

    @staticmethod
    def _validate_provider(provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized not in SUPPORTED_PROVIDERS:
            raise UnsupportedProviderError(provider)
        return normalized

    async def get_instance(self, user_id: str) -> InstanceInfo:
        """
        Get instance info for Mattermost user ID (current default provider).

        Args:
            user_id: Mattermost user ID.

        Returns:
            InstanceInfo with URL and device credentials.

        Raises:
            InstanceNotFoundError: if no active assignment exists.
        """
        return await self.get_instance_by_identity(DEFAULT_PROVIDER, user_id)

    @cached(cache=_identity_cache)
    async def get_instance_by_identity(
        self,
        provider: str,
        provider_user_id: str,
    ) -> InstanceInfo:
        """
        Get OpenClaw instance by channel identity (provider + provider_user_id).
        """
        provider = self._validate_provider(provider)
        async with async_session_factory() as session:
            stmt = (
                select(Instance)
                .join(UserInstance, UserInstance.instance_uuid == Instance.instance_uuid)
                .join(AppUser, AppUser.id == UserInstance.user_id)
                .join(UserIdentity, UserIdentity.user_id == AppUser.id)
                .where(UserIdentity.provider == provider)
                .where(UserIdentity.provider_user_id == provider_user_id)
            )
            result = await session.execute(stmt)
            instance = result.scalar_one_or_none()

            if instance is None:
                logger.warning(
                    "instance_not_found_by_identity",
                    provider=provider,
                    provider_user_id=provider_user_id,
                )
                raise InstanceNotFoundError(f"{provider}:{provider_user_id}")
            logger.debug(
                "instance_resolved_by_identity",
                provider=provider,
                provider_user_id=provider_user_id,
                instance_url=instance.instance_url,
            )
            return InstanceInfo(
                instance_url=instance.instance_url,
                credentials=DeviceCredentials(
                    device_id=instance.device_id,
                    public_key_b64=instance.public_key_b64,
                    private_key_b64=instance.private_key_b64,
                    device_token=instance.device_token,
                    gateway_token=instance.gateway_token,
                ),
            )

    @cached(cache=_external_id_cache)
    async def get_instance_by_external_id(
        self,
        external_user_id: str,
        provider: str = DEFAULT_PROVIDER,
    ) -> tuple[str, InstanceInfo]:
        """
        Resolve provider user ID + instance by external user ID.

        Args:
            external_user_id: External user identifier.
            provider: Identity provider (currently: mattermost).

        Returns:
            Tuple of (provider_user_id, InstanceInfo).

        Raises:
            InstanceNotFoundError: if no mapping exists.
        """
        provider = self._validate_provider(provider)
        async with async_session_factory() as session:
            stmt = (
                select(AppUser, UserIdentity.provider_user_id, Instance)
                .join(UserIdentity, UserIdentity.user_id == AppUser.id)
                .join(UserInstance, UserInstance.user_id == AppUser.id)
                .join(Instance, Instance.instance_uuid == UserInstance.instance_uuid)
                .where(AppUser.external_user_id == external_user_id)
                .where(UserIdentity.provider == provider)
            )
            result = await session.execute(stmt)
            row = result.one_or_none()

            if row is None:
                logger.warning(
                    "instance_not_found_by_external_id",
                    external_user_id=external_user_id,
                    provider=provider,
                )
                raise InstanceNotFoundError(f"{provider}:{external_user_id}")

            user, provider_user_id, instance = row

            logger.debug(
                "instance_resolved_by_external_id",
                external_user_id=external_user_id,
                provider=provider,
                app_user_id=user.id,
                provider_user_id=provider_user_id,
                instance_url=instance.instance_url,
            )
            return provider_user_id, InstanceInfo(
                instance_url=instance.instance_url,
                credentials=DeviceCredentials(
                    device_id=instance.device_id,
                    public_key_b64=instance.public_key_b64,
                    private_key_b64=instance.private_key_b64,
                    device_token=instance.device_token,
                    gateway_token=instance.gateway_token,
                ),
            )

    def invalidate_identity_cache(self) -> None:
        """Clear cached identity lookups after a mapping change."""
        _identity_cache.clear()

    def invalidate_external_id_cache(self) -> None:
        """Clear cached external_id lookups after a mapping change."""
        _external_id_cache.clear()

    def invalidate_cache(self) -> None:
        """Clear all mapping caches."""
        self.invalidate_identity_cache()
        self.invalidate_external_id_cache()
