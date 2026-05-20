"""
Proxy for Mattermost Interactive Messages (Buttons).

Cloud Mattermost instances cannot reach the internal tools-server directly.
This endpoint allows ClawMux (which is already exposed to the public internet)
to receive button clicks from Mattermost and securely proxy them to the
internal tools-server for database updates and agent triggering.
"""

import httpx
import structlog
from fastapi import APIRouter, Request, HTTPException

from src.core.config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["mattermost"])


@router.post("/mm/action")
async def proxy_mm_action(request: Request):
    """
    Proxy Mattermost button click to internal tools-server.
    """
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
