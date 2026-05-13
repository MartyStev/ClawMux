"""
WS Router — Control-Plane API.

POST /api/v1/trigger

Fire-and-forget: внешняя система отправляет задачу конкретному пользователю.
OpenClaw получает задачу и сам пишет ответ пользователю через
обычный канал (Mattermost / proactive callback).

Аутентификация: заголовок X-Api-Token (значение из env API_TOKEN).

Маршрутизация: запрос содержит `external_user_id` — внешний идентификатор пользователя.
Роутер ищет строку в БД по `external_user_id` и получает внутренний Mattermost `user_id`
для подключения к нужному инстансу OpenClaw.
"""
import asyncio
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, model_validator

from src.core.config import settings
from src.services.mapping import InstanceNotFoundError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["control-plane"])


# ── Models ────────────────────────────────────────────────────────


class TriggerRequest(BaseModel):
    external_user_id: str
    text: str
    session_key: Optional[str] = None  # default: "agent:main:main"

    @model_validator(mode="before")
    @classmethod
    def _alias_legacy_crm_user_id(cls, data):
        """
        Backward compatibility: accept old payload key `crm_user_id`.
        """
        if isinstance(data, dict):
            if "external_user_id" not in data and "crm_user_id" in data:
                data["external_user_id"] = data["crm_user_id"]
        return data


class TriggerResponse(BaseModel):
    status: str       # "sent"
    request_id: str   # UUID для трассировки в логах


# ── Endpoint ─────────────────────────────────────────────────────


@router.post("/trigger", response_model=TriggerResponse)
async def trigger(
    req: TriggerRequest,
    request: Request,
    x_api_token: str = Header(..., alias="x-api-token"),
) -> TriggerResponse:
    """
    Отправить задачу пользователю в OpenClaw (fire-and-forget).

    - Принимает `external_user_id` — внешний идентификатор пользователя.
    - Роутер находит нужный инстанс OpenClaw по `external_user_id` через БД.
    - Возвращает {"status": "sent"} немедленно.
    - OpenClaw обрабатывает задачу и сам пишет ответ пользователю.
    - Требует заголовок X-Api-Token.
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
    log = logger.bind(external_user_id=req.external_user_id, request_id=request_id)

    # ── Resolve: external_user_id → Mattermost user_id + InstanceInfo ──
    try:
        mm_user_id, info = await mapping.get_instance_by_external_id(req.external_user_id)
    except InstanceNotFoundError:
        log.warning("trigger_external_user_not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No OpenClaw instance configured for external_user_id={req.external_user_id!r}",
        )

    log = log.bind(mm_user_id=mm_user_id)

    # ── Dispatch to Router for processing and UI feedback ────────
    app_router = request.app.state.router
    
    # We pass the trigger logic to the router so it can:
    # 1. Resolve the correct session_key from the Mattermost channel
    # 2. Show a streaming placeholder ("⏳ Думаю (API задача)...")
    # 3. Handle the response
    asyncio.create_task(
        app_router.trigger_message(
            user_id=mm_user_id,
            info=info,
            text=req.text,
            session_key=req.session_key,
        ),
        name=f"trigger-{request_id[:8]}"
    )

    log.info(
        "trigger_dispatched",
        session_key_requested=req.session_key,
        text_len=len(req.text),
    )
    return TriggerResponse(status="sent", request_id=request_id)
