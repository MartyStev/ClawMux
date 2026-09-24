from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.mapping import router as mapping_router
from src.utils.health import health_router, init_health


def test_reload_mappings_requires_auth(monkeypatch):
    monkeypatch.setattr("src.api.mapping.settings.api_token", "secret-token")

    app = FastAPI()
    app.include_router(mapping_router)
    client = TestClient(app)

    # Missing token
    resp = client.post("/api/v1/mappings/reload")
    assert resp.status_code == 422

    # Wrong token
    resp = client.post("/api/v1/mappings/reload", headers={"x-api-token": "wrong-token"})
    assert resp.status_code == 401


def test_reload_mappings_success(monkeypatch):
    monkeypatch.setattr("src.api.mapping.settings.api_token", "secret-token")

    app = FastAPI()
    app.include_router(mapping_router)
    mapping = MagicMock()
    mapping.reload_cache_version = AsyncMock(return_value=7)
    app.state.mapping = mapping
    client = TestClient(app)

    resp = client.post("/api/v1/mappings/reload", headers={"x-api-token": "secret-token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["message"] == "mapping cache invalidated"
    mapping.reload_cache_version.assert_awaited_once()


def test_health_liveness():
    app = FastAPI()
    app.include_router(health_router)
    client = TestClient(app)

    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "clawmux"}


def test_health_ready_when_dependencies_ok(monkeypatch):
    # Mock DB check
    @asynccontextmanager
    async def fake_session_factory():
        mock_sess = AsyncMock()
        yield mock_sess

    monkeypatch.setattr("src.utils.health.async_session_factory", fake_session_factory)

    mock_ws = MagicMock()
    mock_ws.active_count = 2

    mock_mm = MagicMock()
    mock_mm.is_ws_connected = True

    init_health(mock_ws, mock_mm)

    app = FastAPI()
    app.include_router(health_router)
    client = TestClient(app)

    resp = client.get("/health/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["database"] == "ok"
    assert data["mattermost_ws"] == "ok"


def test_health_ready_when_degraded(monkeypatch):
    # Mock failing DB
    def failing_session_factory():
        raise ConnectionRefusedError("DB is down")

    monkeypatch.setattr("src.utils.health.async_session_factory", failing_session_factory)

    mock_ws = MagicMock()
    mock_mm = MagicMock()
    mock_mm.is_ws_connected = False

    init_health(mock_ws, mock_mm)

    app = FastAPI()
    app.include_router(health_router)
    client = TestClient(app)

    resp = client.get("/health/ready")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert "down" in data["database"]
    assert data["mattermost_ws"] == "disconnected"
