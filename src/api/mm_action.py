"""
Proxy for Mattermost Interactive Messages (Buttons).

Cloud Mattermost instances cannot reach the internal tools-server directly.
This endpoint allows ClawMux (which is already exposed to the public internet)
to receive button clicks from Mattermost and securely proxy them to the
internal tools-server for database updates and agent triggering.
"""

import secrets

import httpx
import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status

from src.core.config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["mattermost"])


@router.post("/mm/action")
async def proxy_mm_action(
    request: Request,
    x_mm_action_secret: str = Header("", alias="x-mm-action-secret"),
):
    """
    Proxy Mattermost button click to internal tools-server.

    Requires the shared secret (MM_ACTION_SHARED_SECRET), passed either via
    the X-MM-Action-Secret header or the ?secret= query param appended to the
    URL registered in Mattermost. Empty secret config = endpoint disabled.
    """
    provided = x_mm_action_secret or request.query_params.get("secret", "")
    if (
        not settings.mm_action_shared_secret
        or not provided
        or not secrets.compare_digest(provided, settings.mm_action_shared_secret)
    ):
        logger.warning("mm_action_unauthorized")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing action secret",
        )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    context = payload.get("context", {})
    logger.info(
        "proxy_mm_action", 
        task_id=context.get("task_id"), 
        action=context.get("action")
    )
    
    # Forward to internal tools-server
    # It must be accessible within the same Docker network (ai-network)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                settings.mm_action_proxy_url,
                json=payload, 
                timeout=10.0
            )
            resp.raise_for_status()
            return resp.json()
            
    except Exception as e:
        logger.error("proxy_mm_action_failed", error=str(e))
        # We must return a valid Mattermost update structure even on failure,
        # otherwise the buttons remain clickable.
        return {"update": {"message": "⚠️ Processing error (backend unavailable). Please try replying with text."}}
