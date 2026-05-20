"""
ClawMux — Notification API.

POST /api/v1/notify

Send a system message directly to a user in Mattermost.
"""
import asyncio

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


class NotifyRequest(BaseModel):
    external_user_id: str
    provider: str = DEFAULT_PROVIDER
    text: str


class NotifyResponse(BaseModel):
    status: str


@router.post("/notify", response_model=NotifyResponse)
async def notify(
    req: NotifyRequest,
    request: Request,
    x_api_token: str = Header(..., alias="x-api-token"),
) -> NotifyResponse:
    """
    Send a system notification directly to a user in Mattermost.
    Finds the user's identity by external_user_id + provider.
    """
    if not settings.api_token or x_api_token != settings.api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )

    provider = req.provider.strip().lower()
    log = logger.bind(external_user_id=req.external_user_id, provider=provider)
    
    mapping = request.app.state.mapping
    app_router = request.app.state.router

    try:
        provider_user_id, _ = await mapping.get_instance_by_external_id(
            req.external_user_id,
            provider=provider,
        )
    except UnsupportedProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider is not enabled: {e.provider!r}",
        )
    except InstanceNotFoundError:
        log.warning("notify_external_user_not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No mapping for external_user_id={req.external_user_id!r}",
        )

    log = log.bind(provider_user_id=provider_user_id)
    
    # Launch the send task in the background without blocking the API response
    asyncio.create_task(
        app_router.handle_proactive(
            user_id=provider_user_id,
            text=req.text,
            provider=provider,
        ),
        name=f"notify-{provider_user_id[:8]}"
    )

    log.info("notify_dispatched", text_len=len(req.text))
    return NotifyResponse(status="sent")
