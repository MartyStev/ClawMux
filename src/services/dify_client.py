"""
ClawMux — Dify Chat API Client.

Fallback handler for users without an OpenClaw instance.
Proxies messages to Dify Chat API (streaming mode) and returns the full answer.

Conversation sessions are persisted per user in memory (TTL-based cache)
so that the same Dify conversation_id is reused across multiple messages.
"""

import asyncio
import json
from typing import Optional, AsyncIterator

import httpx
import structlog
from cachetools import TTLCache

logger = structlog.get_logger(__name__)

# Per-user Dify conversation IDs — kept for 24h of inactivity
_conversation_cache: TTLCache[str, str] = TTLCache(maxsize=5000, ttl=86400)


class DifyClient:
    """
    Client for Dify Chat App API.

    Sends messages via POST /chat-messages (streaming) and aggregates
    the full answer from the SSE stream. Maintains per-user conversation_id.
    """

    def __init__(self, base_url: str, api_key: str, timeout_sec: int = 120):
        """
        Args:
            base_url: Dify API base URL, e.g. ``http://localhost:8081/v1``
            api_key:  Dify application API key (Bearer token).
            timeout_sec: Max seconds to wait for the full streamed response.
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_sec
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def chat(
        self,
        user_id: str,
        message: str,
        inputs: Optional[dict] = None,
    ) -> str:
        """
        Send a message to Dify and return the complete answer.

        Automatically reuses the existing Dify conversation for the user
        (via cached conversation_id) to maintain context across messages.

        Args:
            user_id:  Stable identifier for the end-user (Mattermost user_id).
            message:  User's text query.
            inputs:   Optional Dify variable inputs (app-specific).

        Returns:
            Full answer text from Dify. Empty string on failure.
        """
        log = logger.bind(user_id=user_id)

        conversation_id = _conversation_cache.get(user_id, "")
        log.debug(
            "dify_chat_start",
            has_conversation=bool(conversation_id),
            conversation_id=conversation_id or "new",
        )

        payload: dict = {
            "query": message,
            "inputs": inputs or {},
            "response_mode": "streaming",
            "user": user_id,
            "conversation_id": conversation_id,
        }

        try:
            answer, new_conv_id = await asyncio.wait_for(
                self._stream_chat(payload, log),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            log.error("dify_timeout", timeout_sec=self._timeout)
            return ""
        except httpx.HTTPError as exc:
            log.error("dify_error", error=str(exc))
            return ""

        # Persist conversation_id so the next message continues the same chat
        if new_conv_id:
            _conversation_cache[user_id] = new_conv_id
            log.debug("dify_conversation_updated", conversation_id=new_conv_id)

        return answer

    def get_conversation_id(self, user_id: str) -> Optional[str]:
        """Return the cached Dify conversation_id for the user, if any."""
        return _conversation_cache.get(user_id)

    def clear_conversation(self, user_id: str) -> None:
        """Reset the Dify conversation for the user (start fresh next time)."""
        _conversation_cache.pop(user_id, None)

    # ──────────────────────────────────────────────────────────────────────────
    # Internal streaming
    # ──────────────────────────────────────────────────────────────────────────

    async def _stream_chat(
        self,
        payload: dict,
        log,
    ) -> tuple[str, str]:
        """
        POST /chat-messages and consume the SSE stream.

        Returns:
            (full_answer, conversation_id)
        """
        url = f"{self._base_url}/chat-messages"
        answer_parts: list[str] = []
        conversation_id = ""

        async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout)) as client:
            async with client.stream(
                "POST", url, headers=self._headers, json=payload
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    log.error(
                        "dify_http_error",
                        status=response.status_code,
                        body=body.decode(errors="replace")[:500],
                    )
                    return "", ""

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue

                    raw = line[len("data:"):].strip()
                    if not raw or raw == "[DONE]":
                        continue

                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        log.warning("dify_sse_parse_error", raw=raw[:200])
                        continue

                    event_type = event.get("event", "")

                    # Capture conversation_id from any event that carries it
                    if not conversation_id and event.get("conversation_id"):
                        conversation_id = event["conversation_id"]

                    if event_type in ("message", "agent_message"):
                        chunk = event.get("answer", "")
                        if chunk:
                            answer_parts.append(chunk)

                    elif event_type == "message_end":
                        # Final event — we have the full answer
                        break

                    elif event_type == "error":
                        log.error(
                            "dify_stream_error",
                            code=event.get("code"),
                            message=event.get("message"),
                        )
                        return "", conversation_id

                    elif event_type in ("tts_message", "tts_message_end", "ping"):
                        # Ignore audio / keepalive events
                        pass

        full_answer = "".join(answer_parts)
        log.debug("dify_stream_done", answer_len=len(full_answer))
        return full_answer, conversation_id
