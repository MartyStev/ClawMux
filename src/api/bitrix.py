"""
ClawMux — Bitrix24 Webhook Receiver.

POST /api/v1/bitrix/event

Receives incoming chat events from Bitrix24 Chat Bot webhook (ONIMBOTMESSAGEADD)
and forwards them to the Router.
"""

import secrets
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request

from src.core.config import settings
from src.services.chat_adapter import ChannelEvent
from src.utils.tasks import fire_and_forget

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["bitrix"])


def _extract_inbound_token(request: Request, payload: dict[str, Any]) -> str:
    """Bitrix sends the webhook token as auth.access_token or a query param."""
    auth = payload.get("auth")
    if isinstance(auth, dict):
        token = auth.get("access_token")
        if isinstance(token, str) and token:
            return token
    for key in ("secure", "access_token", "code"):
        token = request.query_params.get(key)
        if token:
            return token
    return ""


@router.post("/bitrix/event")
async def bitrix_webhook_event(request: Request) -> dict[str, Any]:
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

    # ── Verify webhook secret (BITRIX_INBOUND_SECRET) ──────────────
    if not settings.bitrix_inbound_secret:
        raise HTTPException(status_code=401, detail="Bitrix webhook is disabled")
    inbound_token = _extract_inbound_token(request, payload)
    if not inbound_token or not secrets.compare_digest(inbound_token, settings.bitrix_inbound_secret):
        logger.warning("bitrix_webhook_bad_secret")
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    # In Bitrix REST events:
    # event: "ONIMBOTMESSAGEADD"
    # data: {"PARAMS": {"FROM_USER_ID": ..., "DIALOG_ID": ..., "MESSAGE": ..., "MESSAGE_ID": ...}}
    # or flattened in some webhook formats
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
        fire_and_forget(
            app_router.handle_event(event),
            name=f"bitrix-msg-{message_id or from_user_id}",
        )

    return {"status": "received"}
