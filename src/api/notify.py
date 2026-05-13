"""
WS Router — Notification API.

POST /api/v1/notify

Отправка системного сообщения напрямую пользователю в Mattermost.
"""
import asyncio
from typing import Optional

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from src.core.config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["control-plane"])


class NotifyRequest(BaseModel):
    crm_user_id: str
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
    Отправить системное уведомление напрямую пользователю в Mattermost.
    Находит Mattermost user_id по переданному crm_user_id.
    """
    if not settings.api_token or x_api_token != settings.api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )

    log = logger.bind(crm_user_id=req.crm_user_id)
    
    mapping = request.app.state.mapping
    app_router = request.app.state.router

    try:
        mm_user_id, _ = await mapping.get_instance_by_crm_id(req.crm_user_id)
    except Exception as e:
        log.warning("notify_crm_user_not_found", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No mapping for crm_user_id={req.crm_user_id!r}",
        )

    log = log.bind(mm_user_id=mm_user_id)
    
    # Запускаем отправку в фоне, не блокируем ответ API
    asyncio.create_task(
        app_router.handle_proactive(
            user_id=mm_user_id,
            text=req.text,
        ),
        name=f"notify-{mm_user_id[:8]}"
    )

    log.info("notify_dispatched", text_len=len(req.text))
    return NotifyResponse(status="sent")
