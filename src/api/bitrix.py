"""
ClawMux — Bitrix24 Webhook Receiver.

POST /api/v1/bitrix/event

Receives incoming chat events from Bitrix24 Chat Bot webhook (ONIMBOTMESSAGEADD)
and forwards them to the Router.
"""

import asyncio
from typing import Any, Dict

import structlog
from fastapi import APIRouter, Request

from src.core.config import settings
from src.services.chat_adapter import ChannelEvent

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["bitrix"])


@router.post("/bitrix/event")
async def bitrix_webhook_event(request: Request) -> Dict[str, Any]:
    """
    Handle incoming Bitrix24 chat bot events.
    """
    # Parse payload (can be JSON or Form-data depending on Bitrix config)
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
    else:
        form = await request.form()
        payload = dict(form)

    # In Bitrix REST events:
    # event: "ONIMBOTMESSAGEADD"
    # data: {"PARAMS": {"FROM_USER_ID": ..., "DIALOG_ID": ..., "MESSAGE": ..., "MESSAGE_ID": ...}}
    # or flattened in some webhook formats
    event_name = payload.get("event") or payload.get("EVENT", "")
    data = payload.get("data") or payload.get("DATA") or {}
    params = data.get("PARAMS") or payload.get("PARAMS") or data

    from_user_id = str(params.get("FROM_USER_ID") or "")
    dialog_id = str(params.get("DIALOG_ID") or "")
    message_text = str(params.get("MESSAGE") or "").strip()
    message_id = str(params.get("MESSAGE_ID") or "")

    # Ignore own messages from the bot
    if settings.bitrix_bot_id and from_user_id == str(settings.bitrix_bot_id):
        return {"status": "ignored_bot_own_message"}

    if not message_text or not from_user_id or not dialog_id:
        return {"status": "ignored_empty"}

    logger.info(
        "bitrix_message_received",
        user_id=from_user_id,
        dialog_id=dialog_id,
        text_len=len(message_text),
        message_id=message_id,
    )

    event = ChannelEvent(
        provider="bitrix",
        user_id=from_user_id,
        channel_id=dialog_id,
        text=message_text,
        post_id=message_id,
        root_id="",
    )

    app_router = getattr(request.app.state, "router", None)
    if app_router:
        asyncio.create_task(
            app_router.handle_event(event),
            name=f"bitrix-msg-{message_id or from_user_id}",
        )

    return {"status": "received"}
