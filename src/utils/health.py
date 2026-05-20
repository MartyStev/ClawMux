"""
ClawMux — Health Check Endpoints.
"""

from typing import Optional

from fastapi import APIRouter, Depends

from src.services.ws_manager import WSConnectionManager

health_router = APIRouter(tags=["health"])

# Module-level reference — set once at startup via init_health()
_ws_manager: Optional[WSConnectionManager] = None


def init_health(ws_manager: WSConnectionManager) -> None:
    """Inject WS manager dependency at application startup."""
    global _ws_manager
    _ws_manager = ws_manager


def _get_ws_manager() -> WSConnectionManager:
    """FastAPI dependency: return the ws_manager singleton."""
    return _ws_manager  # type: ignore[return-value]


@health_router.get("/health")
async def health():
    """Basic health check."""
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
