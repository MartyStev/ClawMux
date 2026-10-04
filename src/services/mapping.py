"""
ClawMux — Mapping Storage.

Provides lookup:
  provider_user_id + provider → (instance_url, device_credentials)
  external_user_id + provider → provider_user_id + instance info
  provider_user_id + provider → last known channel (proactive delivery)

Reads from tables: instance, app_user, user_identity, user_instance, user_channel.

The instance lookups are cached per-process, but every entry is tagged with the
global `mapping_state.version`. Mutations (bind_user_instance) bump that version
in the same transaction, so stale entries are reloaded on the next read in ANY
replica — cache invalidation no longer depends on the writing process.
"""

import time
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import insert, select, update
from sqlalchemy.dialects import postgresql, sqlite

from src.core.config import settings
from src.core.database import async_session_factory
from src.core.models import (
    AppUser,
    Instance,
    MappingState,
    UserChannel,
    UserIdentity,
    UserInstance,
)

logger = structlog.get_logger(__name__)
DEFAULT_PROVIDER = "mattermost"
SUPPORTED_PROVIDERS = {
    "mattermost",
    "telegram",
    "bitrix",
    "slack",
    "vk_teams",
    "teams",
}

_MAX_CACHE_ENTRIES = 1000


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


# cache entry: (global mapping version, expiry epoch, value)
_CacheValue = tuple[int, float, object]


class MappingStorage:
    """Reads user → instance mapping from PostgreSQL (3NF schema)."""

    def __init__(self) -> None:
        self._identity_cache: dict[tuple[str, str], _CacheValue] = {}
        self._external_id_cache: dict[tuple[str, str], _CacheValue] = {}

    @staticmethod
    def _validate_provider(provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized not in SUPPORTED_PROVIDERS:
            raise UnsupportedProviderError(provider)
        return normalized

    # ── Cache internals ────────────────────────────────────────────

    @staticmethod
    def _state_upsert(session, *, mode: str):
        """INSERT ... ON CONFLICT statement for the mapping_state singleton.

        mode: 'bump' — version = version + 1; 'noop' — create only if absent.
        """
        table: Any = MappingState.__table__
        dialect = session.get_bind().dialect.name
        stmt: Any
        if dialect == "postgresql":
            stmt = postgresql.insert(table)
        elif dialect == "sqlite":
            stmt = sqlite.insert(table)
        else:
            raise RuntimeError(f"Unsupported dialect for mapping_state upsert: {dialect}")
        stmt = stmt.values(id=1, version=1)
        if mode == "bump":
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={"version": table.c.version + 1},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
        return stmt

    async def _cache_version(self, session) -> int:
        """Current global mapping-cache version (creates the singleton row lazily)."""
        version = await session.scalar(select(MappingState.version).where(MappingState.id == 1))
        if version is None:
            await session.execute(self._state_upsert(session, mode="noop"))
            version = await session.scalar(select(MappingState.version).where(MappingState.id == 1))
        return int(version)

    def _cache_get(self, cache: dict, key: tuple, version: int) -> object | None:
        entry = cache.get(key)
        if entry is None:
            return None
        cached_version, expires_at, value = entry
        if time.monotonic() > expires_at or cached_version != version:
            cache.pop(key, None)
            return None
        return value

    def _cache_put(self, cache: dict, key: tuple, version: int, value: object) -> None:
        if len(cache) >= _MAX_CACHE_ENTRIES:
            cache.clear()
        cache[key] = (version, time.monotonic() + settings.mapping_cache_ttl_sec, value)

    async def _bump_version(self, session) -> None:
        """Atomically increment the global cache version inside the caller's transaction."""
        await session.execute(self._state_upsert(session, mode="bump"))

    # ── Lookups ────────────────────────────────────────────────────

    async def get_instance(self, user_id: str) -> InstanceInfo:
        """
        Get instance info for Mattermost user ID (current default provider).

        Raises:
            InstanceNotFoundError: if no active assignment exists.
        """
        return await self.get_instance_by_identity(DEFAULT_PROVIDER, user_id)

    async def get_instance_by_identity(
        self,
        provider: str,
        provider_user_id: str,
    ) -> InstanceInfo:
        """
        Get OpenClaw instance by channel identity (provider + provider_user_id).
        """
        provider = self._validate_provider(provider)
        key = (provider, provider_user_id)
        async with async_session_factory() as session:
            version = await self._cache_version(session)
            cached = self._cache_get(self._identity_cache, key, version)
            if cached is not None:
                return cached  # type: ignore[return-value]

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
            info = InstanceInfo(
                instance_url=instance.instance_url,
                credentials=DeviceCredentials(
                    device_id=instance.device_id,
                    public_key_b64=instance.public_key_b64,
                    private_key_b64=instance.private_key_b64,
                    device_token=instance.device_token,
                    gateway_token=instance.gateway_token,
                ),
            )
            self._cache_put(self._identity_cache, key, version, info)
            return info

    async def get_instance_by_external_id(
        self,
        external_user_id: str,
        provider: str = DEFAULT_PROVIDER,
    ) -> tuple[str, InstanceInfo]:
        """
        Resolve provider user ID + instance by external user ID.

        Raises:
            InstanceNotFoundError: if no mapping exists.
        """
        provider = self._validate_provider(provider)
        key = (external_user_id, provider)
        async with async_session_factory() as session:
            version = await self._cache_version(session)
            cached = self._cache_get(self._external_id_cache, key, version)
            if cached is not None:
                return cached  # type: ignore[return-value]

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
            value = (
                provider_user_id,
                InstanceInfo(
                    instance_url=instance.instance_url,
                    credentials=DeviceCredentials(
                        device_id=instance.device_id,
                        public_key_b64=instance.public_key_b64,
                        private_key_b64=instance.private_key_b64,
                        device_token=instance.device_token,
                        gateway_token=instance.gateway_token,
                    ),
                ),
            )
            self._cache_put(self._external_id_cache, key, version, value)
            return value

    # ── Cache invalidation ─────────────────────────────────────────

    def invalidate_identity_cache(self) -> None:
        """Clear cached identity lookups (local process only)."""
        self._identity_cache.clear()

    def invalidate_external_id_cache(self) -> None:
        """Clear cached external_id lookups (local process only)."""
        self._external_id_cache.clear()

    def invalidate_cache(self) -> None:
        """Clear all internal caches."""
        self.invalidate_identity_cache()
        self.invalidate_external_id_cache()

    async def reload_cache_version(self) -> int:
        """
        Re-read the global version and drop entries produced by an older one.
        Used by POST /api/v1/mappings/reload after external DB edits.
        """
        async with async_session_factory() as session:
            version = await self._cache_version(session)
        for cache in (self._identity_cache, self._external_id_cache):
            for key in [k for k, (v, _, _) in cache.items() if v != version]:
                cache.pop(key, None)
        return version

    # ── Channel bookkeeping (proactive delivery) ───────────────────

    async def remember_channel(
        self,
        provider: str,
        provider_user_id: str,
        channel_id: str,
    ) -> None:
        """Persist the last known channel for a provider identity (upsert)."""
        provider = self._validate_provider(provider)
        async with async_session_factory() as session:
            async with session.begin():
                updated = await session.execute(
                    update(UserChannel)
                    .where(UserChannel.provider == provider)
                    .where(UserChannel.provider_user_id == provider_user_id)
                    .values(channel_id=channel_id)
                )
                if updated.rowcount == 0:
                    await session.execute(
                        insert(UserChannel).values(
                            provider=provider,
                            provider_user_id=provider_user_id,
                            channel_id=channel_id,
                        )
                    )

    async def get_channel(
        self,
        provider: str,
        provider_user_id: str,
    ) -> str | None:
        """Last known channel for a provider identity, or None."""
        provider = self._validate_provider(provider)
        async with async_session_factory() as session:
            return await session.scalar(
                select(UserChannel.channel_id)
                .where(UserChannel.provider == provider)
                .where(UserChannel.provider_user_id == provider_user_id)
            )

    # ── Mutations ──────────────────────────────────────────────────

    async def bind_user_instance(
        self,
        provider: str,
        provider_user_id: str,
        instance_uuid: str,
        instance_url: str,
        credentials: DeviceCredentials,
        external_user_id: str | None = None,
        role: str | None = "user",
    ) -> InstanceInfo:
        """
        Create or update AppUser, UserIdentity, Instance, and UserInstance in 3NF DB schema.
        Bumps the global cache version so every replica reloads on next read.
        """
        provider = self._validate_provider(provider)
        app_user_id = f"{provider}:{provider_user_id}"
        ext_user_id = external_user_id or app_user_id

        async with async_session_factory() as session:
            async with session.begin():
                # 1. Upsert Instance
                stmt_inst = select(Instance).where(Instance.instance_uuid == instance_uuid)
                res_inst = await session.execute(stmt_inst)
                inst = res_inst.scalar_one_or_none()

                if inst is None:
                    inst = Instance(
                        instance_uuid=instance_uuid,
                        instance_url=instance_url,
                        device_id=credentials.device_id,
                        public_key_b64=credentials.public_key_b64,
                        private_key_b64=credentials.private_key_b64,
                        device_token=credentials.device_token,
                        gateway_token=credentials.gateway_token,
                    )
                    session.add(inst)
                else:
                    inst.instance_url = instance_url
                    inst.device_id = credentials.device_id
                    inst.public_key_b64 = credentials.public_key_b64
                    inst.private_key_b64 = credentials.private_key_b64
                    inst.device_token = credentials.device_token
                    inst.gateway_token = credentials.gateway_token

                # 2. Upsert AppUser
                stmt_user = select(AppUser).where(AppUser.id == app_user_id)
                res_user = await session.execute(stmt_user)
                user = res_user.scalar_one_or_none()

                if user is None:
                    user = AppUser(
                        id=app_user_id,
                        external_user_id=ext_user_id,
                        role=role,
                    )
                    session.add(user)
                elif user.role is None and role is not None:
                    # Preserve a deliberate role, but persist the template role
                    # selected during provisioning for an unassigned user.
                    user.role = role

                # 3. Upsert UserIdentity
                stmt_id = select(UserIdentity).where(
                    UserIdentity.user_id == app_user_id,
                    UserIdentity.provider == provider,
                )
                res_id = await session.execute(stmt_id)
                ident = res_id.scalar_one_or_none()

                if ident is None:
                    ident = UserIdentity(
                        user_id=app_user_id,
                        provider=provider,
                        provider_user_id=provider_user_id,
                    )
                    session.add(ident)

                # 4. Upsert UserInstance
                stmt_ui = select(UserInstance).where(UserInstance.user_id == app_user_id)
                res_ui = await session.execute(stmt_ui)
                ui = res_ui.scalar_one_or_none()

                if ui is None:
                    ui = UserInstance(
                        user_id=app_user_id,
                        instance_uuid=instance_uuid,
                    )
                    session.add(ui)
                else:
                    ui.instance_uuid = instance_uuid

                # 5. Bump the global cache version (same transaction)
                await self._bump_version(session)

        # Our own cached entries are now stale by definition; other replicas
        # detect the version mismatch on their next read.
        self.invalidate_cache()
        logger.info(
            "user_instance_bound_successfully",
            provider=provider,
            provider_user_id=provider_user_id,
            instance_uuid=instance_uuid,
        )
        return InstanceInfo(instance_url=instance_url, credentials=credentials)
