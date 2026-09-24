"""Tests for reliability fixes (review backlog item 2).

Covers:
  - fire_and_forget(): strong refs + error logging for background tasks
  - WSConnectionManager: cleanup loop started via start(), not __init__
  - TeamsAdapter: conversation→serviceUrl map bounded with LRU eviction
  - TelegramAdapter: bot token redacted from logs and raised exceptions
  - /health/ready: fail closed when an adapter cannot report WS state
  - ClawAggregator.is_valid_text: threshold configurable via settings
"""

import asyncio
import gc
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.services.teams as teams_mod
from src.core.config import settings
from src.services.telegram import TelegramAdapter
from src.services.teams import TeamsAdapter
from src.services.ws_manager import WSConnectionManager
from src.utils.claw_aggregator import is_valid_text
from src.utils import tasks as tasks_mod
from src.utils.health import health_router, init_health


# ── fire_and_forget ───────────────────────────────────────────────


def test_fire_and_forget_keeps_strong_ref_until_done():
    async def run():
        flag = {"ran": False}

        async def work():
            await asyncio.sleep(0.05)  # yield so GC could reap an unreferenced task
            flag["ran"] = True

        tasks_mod.fire_and_forget(work(), name="ff-ref")
        gc.collect()
        assert len(tasks_mod._background_tasks) == 1
        await asyncio.sleep(0.2)
        assert flag["ran"]
        assert len(tasks_mod._background_tasks) == 0

    asyncio.run(run())


def test_fire_and_forget_retrieves_and_logs_errors():
    async def run():
        async def boom():
            raise RuntimeError("kaboom")

        task = tasks_mod.fire_and_forget(boom(), name="ff-error")
        await asyncio.sleep(0.05)
        assert task.done()
        assert isinstance(task.exception(), RuntimeError)
        # done-callback already retrieved it → registry cleaned up
        assert len(tasks_mod._background_tasks) == 0

    asyncio.run(run())


# ── WSConnectionManager lifecycle ─────────────────────────────────


def test_manager_does_not_start_cleanup_in_init():
    async def run():
        mgr = WSConnectionManager()
        try:
            assert mgr._cleanup_task is None
        finally:
            await mgr.close_all()

    asyncio.run(run())


def test_manager_start_spawns_cleanup_and_close_all_stops_it():
    async def run():
        mgr = WSConnectionManager()
        await mgr.start()
        assert mgr._cleanup_task is not None
        assert not mgr._cleanup_task.done()
        await mgr.close_all()
        assert mgr._cleanup_task is None

    asyncio.run(run())


def test_manager_close_all_without_start_is_safe():
    async def run():
        await WSConnectionManager().close_all()

    asyncio.run(run())


# ── Teams serviceUrl LRU ──────────────────────────────────────────


def test_teams_service_urls_are_lru_bounded(monkeypatch):
    monkeypatch.setattr(teams_mod, "_MAX_SERVICE_URLS", 3)
    adapter = TeamsAdapter(app_id="app", app_password="pw")

    for i in range(5):
        adapter.register_service_url(f"conv-{i}", f"https://smba.trafficmanager.net/teams/{i}")

    assert len(adapter._service_urls) == 3
    assert adapter.get_service_url("conv-0") == "https://smba.trafficmanager.net/teams"  # evicted → fallback
    assert adapter.get_service_url("conv-4").endswith("/4")
    # LRU touch via get: conv-2 becomes most recently used...
    adapter.get_service_url("conv-2")
    for i in (5, 6):
        adapter.register_service_url(f"conv-{i}", f"https://smba.trafficmanager.net/teams/{i}")
    assert "conv-2" in adapter._service_urls
    assert "conv-3" not in adapter._service_urls


def test_teams_rejects_non_whitelisted_service_url():
    adapter = TeamsAdapter(app_id="app", app_password="pw")
    adapter.register_service_url("conv-x", "https://evil.example.com/teams")
    assert "conv-x" not in adapter._service_urls
    assert adapter.get_service_url("conv-x") == "https://smba.trafficmanager.net/teams"


# ── Telegram token redaction ──────────────────────────────────────

TOKEN = "123456789:AA-s3cr3t-tok3n-XYZ"


def _telegram_adapter() -> TelegramAdapter:
    return TelegramAdapter(bot_token=TOKEN)


def test_telegram_redact_strips_token_from_error():
    adapter = _telegram_adapter()
    raw = f"Client error '400 Bad Request' for url 'https://api.telegram.org/bot{TOKEN}/sendMessage'"
    redacted = adapter._redact(raw)
    assert TOKEN not in redacted
    assert "***" in redacted


def test_telegram_check_raises_redacted_error():
    adapter = _telegram_adapter()
    request = httpx.Request("POST", f"https://api.telegram.org/bot{TOKEN}/sendMessage")
    resp = httpx.Response(400, request=request)
    with pytest.raises(httpx.HTTPError) as exc_info:
        adapter._check(resp)
    assert TOKEN not in str(exc_info.value)


def test_telegram_send_reply_raises_redacted_error():
    async def run():
        adapter = _telegram_adapter()

        class _Client:
            async def post(self, url, **kwargs):
                raise httpx.ConnectError(f"failed to reach {url}")

        adapter._http_client = _Client()
        with pytest.raises(httpx.HTTPError) as exc_info:
            await adapter.send_reply("42", "hi")
        assert TOKEN not in str(exc_info.value)

    asyncio.run(run())


# ── Health readiness fail-closed ──────────────────────────────────


@asynccontextmanager
async def _fake_db_session():
    yield AsyncMock()


def _health_client() -> TestClient:
    app = FastAPI()
    app.include_router(health_router)
    return TestClient(app)


def test_health_ready_fails_closed_without_ws_attribute(monkeypatch):
    monkeypatch.setattr("src.utils.health.async_session_factory", _fake_db_session)
    # DB is fine, but the mattermost object cannot report WS state → not ready
    init_health(MagicMock(), mattermost=object(), providers=None)
    resp = _health_client().get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["mattermost_ws"] == "disconnected"


def test_health_ready_ok_when_ws_connected(monkeypatch):
    monkeypatch.setattr("src.utils.health.async_session_factory", _fake_db_session)
    mm = MagicMock()
    mm.is_ws_connected = True
    init_health(MagicMock(), mattermost=mm, providers=None)
    resp = _health_client().get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["mattermost_ws"] == "ok"


# ── ClawAggregator validity threshold ─────────────────────────────


def test_valid_text_threshold_follows_setting(monkeypatch):
    monkeypatch.setattr(settings, "claw_min_valid_text_len", 2)
    assert is_valid_text("hi")
    monkeypatch.setattr(settings, "claw_min_valid_text_len", 5)
    assert not is_valid_text("hi")
    assert is_valid_text("hello there")
    # exact junk tokens stay invalid even when long enough
    assert not is_valid_text("thinking...")
