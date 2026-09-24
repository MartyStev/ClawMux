"""
ClawMux — Microsoft Teams Webhook Receiver.

POST /api/v1/teams/messages

Receives incoming activities from Azure Bot Service / Bot Framework Connector
and forwards them to the multi-channel Router.
"""

from typing import Any, Dict

import structlog
from fastapi import APIRouter, HTTPException, Request, Response

from src.services.chat_adapter import ChannelEvent
from src.services.teams import TeamsAdapter
from src.services.teams_auth import TeamsAuthError, is_allowed_service_url, verify_inbound_token
from src.utils.tasks import fire_and_forget

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["teams"])


@router.post("/teams/messages")
async def teams_webhook_messages(request: Request) -> Response:
    """
    Receive activities from Azure Bot Framework.
    """
    # ── Verify Bot Framework JWT (signature / aud / iss / exp) ──────
    try:
        verify_inbound_token(request.headers.get("authorization"))
    except TeamsAuthError as e:
        logger.warning("teams_webhook_unauthorized", error=str(e))
        raise HTTPException(status_code=401, detail="Unauthorized activity")

    try:
        activity = await request.json()
    except Exception:
        return Response(status_code=400)

    activity_type = activity.get("type")
    if activity_type != "message":
        # Accept other activity types (ping, conversationUpdate) with 200 OK
        return Response(status_code=200)

    from_user = activity.get("from", {})
    user_id = str(from_user.get("id") or "")
    conversation = activity.get("conversation", {})
    conversation_id = str(conversation.get("id") or "")
    text = str(activity.get("text") or "").strip()
    msg_id = str(activity.get("id") or "")
    reply_to_id = str(activity.get("replyToId") or "")
    service_url = str(activity.get("serviceUrl") or "")

    if not user_id or not conversation_id or not text:
        return Response(status_code=200)

    # ── Anti-SSRF: only trusted Bot Framework hosts may receive our Bearer token ──
    if not is_allowed_service_url(service_url):
        logger.warning(
            "teams_service_url_rejected",
            service_url=service_url[:120],
            conversation_id=conversation_id,
        )
        raise HTTPException(status_code=403, detail="serviceUrl host is not allowed")

    # Save serviceUrl on TeamsAdapter if registered
    registry = getattr(request.app.state, "registry", None)
    if registry:
        adapter = registry.get("teams")
        if isinstance(adapter, TeamsAdapter) and service_url:
            adapter.register_service_url(conversation_id, service_url)

    logger.info(
        "teams_activity_received",
        user_id=user_id,
        conversation_id=conversation_id,
        text_len=len(text),
        msg_id=msg_id,
    )

    # Check for attachments
    attachments = activity.get("attachments", [])
    file_ids = [str(a.get("name", i)) for i, a in enumerate(attachments)]

    event = ChannelEvent(
        provider="teams",
        user_id=user_id,
        channel_id=conversation_id,
        text=text,
        post_id=msg_id,
        file_ids=file_ids,
        root_id=reply_to_id,
    )

    router_instance = getattr(request.app.state, "router", None)
    if router_instance:
        fire_and_forget(
            router_instance.handle_event(event),
            name=f"teams-msg-{event.user_id[:8] if event.user_id else 'anon'}",
        )

    return Response(status_code=200)
