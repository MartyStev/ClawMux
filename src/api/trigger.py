"""
ClawMux — Control-Plane API.

POST /api/v1/trigger

Fire-and-forget: an external system sends a task for a specific user.
OpenClaw receives the task and replies to the user via the regular channel
(Mattermost / proactive callback).

Authentication: `X-Api-Token` header (value from env `API_TOKEN`).

Routing: the request contains `external_user_id` as the user's external identifier.
The router looks up the DB row by (`external_user_id`, `provider`) and resolves
the provider-specific `user_id` used to connect to the target OpenClaw instance.
"""
import asyncio
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from src.core.config import settings
from src.services.mapping import (
    DEFAULT_PROVIDER,
    InstanceNotFoundError,
    UnsupportedProviderError,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["control-plane"])


# ── Models ────────────────────────────────────────────────────────


class TriggerRequest(BaseModel):
    external_user_id: str
    provider: str = DEFAULT_PROVIDER
    text: str
    session_key: Optional[str] = None  # default: "agent:main:main"


class TriggerResponse(BaseModel):
    status: str       # "sent"
    request_id: str   # UUID for tracing in logs


# ── Endpoint ─────────────────────────────────────────────────────


@router.post("/trigger", response_model=TriggerResponse)
async def trigger(
    req: TriggerRequest,
    request: Request,
    x_api_token: str = Header(..., alias="x-api-token"),
) -> TriggerResponse:
    """
    Send a task to a user in OpenClaw (fire-and-forget).

    - Accepts `external_user_id` and `provider`.
    - Router finds the correct OpenClaw instance through the DB.
    - Returns {"status": "sent"} immediately.
    - OpenClaw processes the task and replies to the user.
    - Requires an `X-Api-Token` header.
    """
    # ── Auth ──────────────────────────────────────────────────────
    if not settings.api_token or x_api_token != settings.api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )

    ws_manager = request.app.state.ws_manager
    mapping = request.app.state.mapping

    request_id = str(uuid.uuid4())
    provider = req.provider.strip().lower()
    log = logger.bind(
        external_user_id=req.external_user_id,
        provider=provider,
        request_id=request_id,
    )

    # ── Resolve: external_user_id + provider → provider_user_id + InstanceInfo ──
    try:
        provider_user_id, info = await mapping.get_instance_by_external_id(
            req.external_user_id,
            provider=provider,
        )
    except UnsupportedProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider is not enabled: {e.provider!r}",
        )
    except InstanceNotFoundError:
        log.warning("trigger_external_user_not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No OpenClaw instance configured for external_user_id={req.external_user_id!r}",
        )

    log = log.bind(provider_user_id=provider_user_id)

    # ── Dispatch to Router for processing and UI feedback ────────
    app_router = request.app.state.router
    
    # We pass the trigger logic to the router so it can:
    # 1. Resolve the correct session_key from the Mattermost channel
    # 2. Show a streaming placeholder ("⏳ Thinking (API task)...")
    # 3. Handle the response
    asyncio.create_task(
        app_router.trigger_message(
            user_id=provider_user_id,
            info=info,
            text=req.text,
            session_key=req.session_key,
            provider=provider,
        ),
        name=f"trigger-{request_id[:8]}"
    )

    log.info(
        "trigger_dispatched",
        session_key_requested=req.session_key,
        text_len=len(req.text),
    )
    return TriggerResponse(status="sent", request_id=request_id)
