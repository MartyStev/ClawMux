"""
ClawMux — FastAPI Application.

Entry point for the WebSocket Router service.
Manages lifecycle of all components:
  - Mattermost WS listener
  - OpenClaw WS connection pool
  - PostgreSQL connection
  - Idle cleanup task
"""

import asyncio
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from src.core.config import settings
from src.core.database import dispose_engine
from src.utils.health import health_router, init_health
from src.services.mapping import MappingStorage
from src.services.mattermost import MattermostClient
from src.api.trigger import router as trigger_router
from src.api.mm_action import router as mm_action_router
from src.api.notify import router as notify_router
from src.router import Router
from src.services.ws_manager import WSConnectionManager

# ── Structured Logging Setup ─────────────────────────────────────
import logging

_log_level_int = getattr(logging, settings.log_level.upper(), logging.INFO)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if settings.log_level == "DEBUG"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(_log_level_int),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown."""

    logger.info(
        "starting",
        mattermost_url=settings.mattermost_url,
        idle_timeout_sec=settings.ws_idle_timeout_sec,
    )

    # ── Initialize components ─────────────────────────────────────
    mapping = MappingStorage()
    mattermost = MattermostClient()

    # Use setter to break circular dependency:
    ws_manager = WSConnectionManager()
    router = Router(mapping, ws_manager, mattermost)
    ws_manager.set_proactive_handler(router.handle_proactive)

    # Expose shared components for API endpoints
    app.state.ws_manager = ws_manager
    app.state.mapping = mapping
    app.state.router = router

    # Inject WS manager into health checks
    init_health(ws_manager)

    # Start Mattermost listener in background
    mm_task = asyncio.create_task(
        mattermost.start(on_message=router.handle_event),
        name="mattermost-listener",
    )

    logger.info("started_ok")

    yield

    # ── Shutdown ──────────────────────────────────────────────
    logger.info("shutting_down")

    await mattermost.stop()
    mm_task.cancel()
    try:
        await mm_task
    except asyncio.CancelledError:
        pass

    await ws_manager.close_all()
    await dispose_engine()

    logger.info("shutdown_complete")


# ── FastAPI App ──────────────────────────────────────────────────
app = FastAPI(
    title="ClawMux",
    description="Routes Mattermost messages to per-user OpenClaw instances via WebSocket",
    version="1.0.0",
    lifespan=lifespan,
)

# Health endpoints
app.include_router(health_router)

# Control-plane API
app.include_router(trigger_router)
app.include_router(notify_router)

# Mattermost webhook proxy
app.include_router(mm_action_router)

# Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
