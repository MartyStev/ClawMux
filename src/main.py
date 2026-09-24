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
from src.services.chat_adapter import ProviderRegistry
from src.services.mapping import MappingStorage
from src.services.mattermost import MattermostClient
from src.services.telegram import TelegramAdapter
from src.services.bitrix import BitrixAdapter
from src.services.slack import SlackAdapter
from src.services.vk_teams import VkTeamsAdapter
from src.services.teams import TeamsAdapter
from src.api.trigger import router as trigger_router
from src.api.mm_action import router as mm_action_router
from src.api.notify import router as notify_router
from src.api.mapping import router as mapping_router
from src.api.bitrix import router as bitrix_router
from src.api.teams import router as teams_router
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
        mattermost_enabled=settings.enable_mattermost,
        telegram_enabled=settings.enable_telegram,
        bitrix_enabled=settings.enable_bitrix,
        idle_timeout_sec=settings.ws_idle_timeout_sec,
    )

    if not settings.credential_encryption_key.strip():
        logger.warning(
            "credential_encryption_disabled",
            hint=(
                "Set CREDENTIAL_ENCRYPTION_KEY (Fernet) to encrypt OpenClaw "
                "private keys and tokens at rest; see SECURITY.md"
            ),
        )

    # ── Initialize components ─────────────────────────────────────
    mapping = MappingStorage()
    registry = ProviderRegistry()

    # Register enabled chat adapters
    if settings.enable_mattermost and settings.mattermost_token:
        mm_adapter = MattermostClient()
        registry.register(mm_adapter)

    if settings.enable_telegram and settings.telegram_bot_token:
        tg_adapter = TelegramAdapter(bot_token=settings.telegram_bot_token)
        registry.register(tg_adapter)

    if settings.enable_bitrix and settings.bitrix_webhook_url:
        bx_adapter = BitrixAdapter(
            webhook_url=settings.bitrix_webhook_url,
            bot_id=settings.bitrix_bot_id,
        )
        registry.register(bx_adapter)

    if settings.enable_slack and settings.slack_bot_token and settings.slack_app_token:
        slack_adapter = SlackAdapter(
            bot_token=settings.slack_bot_token,
            app_token=settings.slack_app_token,
        )
        registry.register(slack_adapter)

    if settings.enable_vk_teams and settings.vk_teams_bot_token:
        vk_adapter = VkTeamsAdapter(
            bot_token=settings.vk_teams_bot_token,
            api_url=settings.vk_teams_api_url,
        )
        registry.register(vk_adapter)

    if settings.enable_teams and settings.teams_app_id:
        teams_adapter = TeamsAdapter(
            app_id=settings.teams_app_id,
            app_password=settings.teams_app_password,
        )
        registry.register(teams_adapter)

    ws_manager = WSConnectionManager()
    router = Router(mapping=mapping, ws_manager=ws_manager, providers=registry)
    ws_manager.set_proactive_handler(router.handle_proactive)

    # Expose shared components for API endpoints
    app.state.ws_manager = ws_manager
    app.state.mapping = mapping
    app.state.router = router
    app.state.registry = registry
    app.state.mattermost = router.mattermost

    # Inject WS manager and provider registry into health checks
    init_health(ws_manager, mattermost=router.mattermost, providers=registry)

    # Start background listener loops for registered adapters
    listener_tasks: list[asyncio.Task] = []
    for adapter in registry.all():
        t = asyncio.create_task(
            adapter.start(on_message=router.handle_event),
            name=f"{adapter.name}-listener",
        )
        listener_tasks.append(t)

    logger.info("started_ok", active_providers=list(registry.supported_providers()))

    yield

    # ── Shutdown ──────────────────────────────────────────────
    logger.info("shutting_down")

    for adapter in registry.all():
        await adapter.stop()

    for t in listener_tasks:
        t.cancel()
    if listener_tasks:
        await asyncio.gather(*listener_tasks, return_exceptions=True)

    await ws_manager.close_all()
    await dispose_engine()

    logger.info("shutdown_complete")


# ── FastAPI App ──────────────────────────────────────────────────
app = FastAPI(
    title="ClawMux",
    description="Multi-channel AI router for OpenClaw instances (Mattermost, Telegram, Bitrix24, Slack, VK Teams, Teams)",
    version="3.0.0",
    lifespan=lifespan,
)

# Health endpoints
app.include_router(health_router)

# Control-plane API
app.include_router(trigger_router)
app.include_router(notify_router)
app.include_router(mapping_router)

# Multi-channel webhooks
app.include_router(mm_action_router)
app.include_router(bitrix_router)
app.include_router(teams_router)

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
