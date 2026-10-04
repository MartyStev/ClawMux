"""
Tests for DB-backed router state (real SQLite, no DB mocks):
  - user_channel: last-known-channel persistence (survives restart)
  - mapping_state: cross-process cache invalidation via global version
"""

import asyncio
import time
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from src.core.config import settings
from src.core.models import AppUser
from src.router import Router
from src.services.chat_adapter import ProviderRegistry
from src.services.mapping import DeviceCredentials, MappingStorage

pytest.importorskip("aiosqlite")


def _creds(suffix: str) -> DeviceCredentials:
    return DeviceCredentials(
        device_id=f"dev-{suffix}",
        public_key_b64=f"pub-{suffix}",
        private_key_b64=f"priv-{suffix}",
        device_token=f"dt-{suffix}",
        gateway_token=f"gt-{suffix}",
    )


def _bind(storage: MappingStorage, user: str, uuid: str, suffix: str):
    return storage.bind_user_instance(
        provider="telegram",
        provider_user_id=user,
        instance_uuid=uuid,
        instance_url=f"ws://openclaw-gw-{suffix}:18789/ws",
        credentials=_creds(suffix),
    )


# ── user_channel persistence ────────────────────────────────────────


def test_remember_and_get_channel_roundtrip(sqlite_db):
    storage = MappingStorage()
    asyncio.run(storage.remember_channel("telegram", "u1", "chat-1"))
    assert asyncio.run(storage.get_channel("telegram", "u1")) == "chat-1"

    # upsert overwrites, no duplicate rows
    asyncio.run(storage.remember_channel("telegram", "u1", "chat-2"))
    assert asyncio.run(storage.get_channel("telegram", "u1")) == "chat-2"

    assert asyncio.run(storage.get_channel("telegram", "unknown-user")) is None


def test_channel_visible_to_fresh_router_after_restart(sqlite_db):
    """A new Router process (empty local cache) finds the channel in the DB."""
    storage = MappingStorage()

    class DummyAdapter:
        name = "telegram"

        def __init__(self):
            self.dm_created = 0

        async def get_or_create_dm_channel(self, user_id: str) -> str:
            self.dm_created += 1
            return "dm-new"

        def get(self, _):  # not used
            return None

    adapter = DummyAdapter()
    registry = ProviderRegistry()
    registry.get = lambda p: adapter  # type: ignore[assignment]

    router1 = Router(mapping=storage, ws_manager=MagicMock(), providers=registry)
    asyncio.run(router1._remember_channel("telegram:u9", "telegram", "u9", "chat-77"))

    # simulate restart: fresh Router, same DB
    router2 = Router(mapping=storage, ws_manager=MagicMock(), providers=registry)
    channel = asyncio.run(router2.get_or_create_channel("telegram:u9", "u9", "telegram"))
    assert channel == "chat-77"
    assert adapter.dm_created == 0  # DB hit — no DM creation needed


def test_get_or_create_channel_falls_back_to_dm_and_persists(sqlite_db):
    storage = MappingStorage()

    class DummyAdapter:
        name = "telegram"

        async def get_or_create_dm_channel(self, user_id: str) -> str:
            return "dm-42"

    adapter = DummyAdapter()
    registry = ProviderRegistry()
    registry.get = lambda p: adapter  # type: ignore[assignment]

    router = Router(mapping=storage, ws_manager=MagicMock(), providers=registry)
    channel = asyncio.run(router.get_or_create_channel("telegram:u10", "u10", "telegram"))
    assert channel == "dm-42"
    # persisted for the next "restart"
    assert asyncio.run(storage.get_channel("telegram", "u10")) == "dm-42"


# ── mapping_state versioned cache ───────────────────────────────────


def test_rebind_bumps_version_and_evicts_other_replica_cache(sqlite_db):
    """s1 and s2 simulate two ClawMux replicas sharing one database."""
    s1 = MappingStorage()
    s2 = MappingStorage()

    asyncio.run(_bind(s1, "u1", "11111111-1111-1111-1111-111111111111", "A"))
    info = asyncio.run(s2.get_instance_by_identity("telegram", "u1"))
    assert info.credentials.device_id == "dev-A"
    assert ("telegram", "u1") in s2._identity_cache  # primed

    # another replica reassigns the user
    asyncio.run(_bind(s1, "u2", "22222222-1111-1111-1111-111111111111", "B"))
    asyncio.run(
        s1.bind_user_instance(
            provider="telegram",
            provider_user_id="u1",
            instance_uuid="33333333-1111-1111-1111-111111111111",
            instance_url="ws://openclaw-gw-C:18789/ws",
            credentials=_creds("C"),
        )
    )

    # s2 must NOT serve its stale cached entry
    info2 = asyncio.run(s2.get_instance_by_identity("telegram", "u1"))
    assert info2.credentials.device_id == "dev-C"


def test_bind_populates_missing_role_without_overwriting_existing_role(sqlite_db):
    async def run() -> tuple[str | None, str | None]:
        async with sqlite_db() as session:
            async with session.begin():
                session.add(AppUser(id="telegram:missing-role", role=None))
                session.add(AppUser(id="telegram:existing-role", role="curator"))

        storage = MappingStorage()
        for user, suffix in (("missing-role", "M"), ("existing-role", "E")):
            await storage.bind_user_instance(
                provider="telegram",
                provider_user_id=user,
                instance_uuid=f"{suffix.lower() * 8}-1111-1111-1111-111111111111",
                instance_url=f"ws://openclaw-gw-{suffix}:18789/ws",
                credentials=_creds(suffix),
                role="sales",
            )

        async with sqlite_db() as session:
            rows = await session.execute(select(AppUser).order_by(AppUser.id))
            roles = {user.id: user.role for user in rows.scalars()}
        return roles["telegram:missing-role"], roles["telegram:existing-role"]

    assert asyncio.run(run()) == ("sales", "curator")


def test_cache_entry_expires_by_ttl(sqlite_db, monkeypatch):
    monkeypatch.setattr(settings, "mapping_cache_ttl_sec", -1)  # instant expiry
    storage = MappingStorage()
    asyncio.run(_bind(storage, "u5", "55555555-1111-1111-1111-111111111111", "E"))
    asyncio.run(storage.get_instance_by_identity("telegram", "u5"))
    entry = storage._identity_cache[("telegram", "u5")]
    assert entry[1] <= time.monotonic()  # expired timestamp
    # expired entry triggers a fresh DB read instead of a hit
    info = asyncio.run(storage.get_instance_by_identity("telegram", "u5"))
    assert info.credentials.device_id == "dev-E"
    assert ("telegram", "u5") in storage._identity_cache  # re-primed


def test_reload_cache_version_drops_stale_entries(sqlite_db):
    storage = MappingStorage()
    asyncio.run(_bind(storage, "u6", "66666666-1111-1111-1111-111111111111", "F"))
    asyncio.run(storage.get_instance_by_identity("telegram", "u6"))
    # simulate an entry produced under an older (external DB edit) version
    key = ("telegram", "u6")
    version, expires, value = storage._identity_cache[key]
    storage._identity_cache[key] = (version + 100, expires, value)

    current = asyncio.run(storage.reload_cache_version())

    assert key not in storage._identity_cache
    assert current >= 1
