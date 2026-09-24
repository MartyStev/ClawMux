import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text

from src.core.database import async_session_factory
from src.services.chat_adapter import ProviderRegistry
from src.services.ws_manager import WSConnectionManager

health_router = APIRouter(tags=["health"])

# Module-level references — set once at startup via init_health()
_ws_manager: WSConnectionManager | None = None
_mattermost: Any | None = None
_providers: ProviderRegistry | None = None


def init_health(
    ws_manager: WSConnectionManager,
    mattermost: Any | None = None,
    providers: ProviderRegistry | None = None,
) -> None:
    """Inject dependencies at application startup."""
    global _ws_manager, _mattermost, _providers
    _ws_manager = ws_manager
    _mattermost = mattermost
    _providers = providers


def _get_ws_manager() -> WSConnectionManager:
    """FastAPI dependency: return the ws_manager singleton."""
    return _ws_manager  # type: ignore[return-value]


@health_router.get("/health")
async def health():
    """Basic health check (liveness probe)."""
    return {
        "status": "ok",
        "service": "clawmux",
    }


@health_router.get("/health/detail")
async def health_detail(manager: WSConnectionManager = Depends(_get_ws_manager)):
    """Detailed health check with connection stats."""
    return {
        "status": "ok",
        "service": "clawmux",
        "active_ws_connections": manager.active_count if manager else 0,
    }


@health_router.get("/health/ready")
async def health_ready(response: Response):
    """Readiness probe checking database and active chat channel adapters."""
    db_ok = False
    db_error = None
    try:

        async def _ping_db():
            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))

        await asyncio.wait_for(_ping_db(), timeout=2.0)
        db_ok = True
    except Exception as e:
        db_error = str(e)

    channels: dict[str, str] = {}
    channels_ok = True

    if _providers is not None and _providers.all():
        for adapter in _providers.all():
            is_conn = adapter.is_connected
            channels[adapter.name] = "ok" if is_conn else "disconnected"
            if not is_conn:
                channels_ok = False
    elif _mattermost is not None:
        # Fail closed: an adapter that doesn't report its WS state is
        # treated as disconnected rather than silently ready.
        mm_ok = bool(getattr(_mattermost, "is_ws_connected", False))
        channels["mattermost"] = "ok" if mm_ok else "disconnected"
        channels_ok = mm_ok
    else:
        channels["default"] = "ok"

    is_ready = db_ok and channels_ok
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    result: dict[str, Any] = {
        "status": "ready" if is_ready else "degraded",
        "service": "clawmux",
        "database": "ok" if db_ok else f"down: {db_error}",
        "channels": channels,
    }
    # Keep backwards-compatible key for existing monitors/tests
    result["mattermost_ws"] = channels.get("mattermost", "ok" if channels_ok else "disconnected")
    return result
