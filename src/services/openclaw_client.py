"""
ClawMux — OpenClaw WebSocket Client.

Implements the OpenClaw Gateway authentication protocol (Ed25519 v2).

Architecture:
  - connect() establishes WS + auth, then starts _listen_loop() background task
  - send_message() sends chat.send and waits on asyncio.Future for the response
  - _listen_loop() dispatches events to ClawAggregator which:
      • buffers all chat.final events per msg_id
      • debounces (CLAW_DEBOUNCE_MS) and picks the highest-seq valid final
      • calls _route_aggregated_message() with the winning ClawMessage
  - _route_aggregated_message() resolves the pending future OR calls on_proactive
  - This eliminates truncated "early final" messages in Mattermost.

Protocol flow:
  1. Connect WS → receive connect.challenge (nonce)
  2. Sign nonce with Ed25519 → send connect request
  3. Receive connect response (ok/error)
  4. [Background] _listen_loop reads all events indefinitely
  5. send_message() drops a chat.send and waits for the aggregated chat.final
"""

import aiofiles
import asyncio
import base64
import json
import re
import os
import time
import uuid
from typing import Awaitable, Callable, Optional

import structlog
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import ClientConnection

from src.utils.claw_aggregator import ClawAggregator, ClawMessage
from src.core.config import settings
from src.services.mapping import DeviceCredentials

logger = structlog.get_logger(__name__)

CLIENT_ID = "openclaw-control-ui"
CLIENT_MODE = "webchat"
PLATFORM = "python-service"
ROLE = "operator"
SCOPES = [
    "operator.admin",
    "operator.read",
    "operator.write",
    "operator.approvals",
    "operator.pairing",
]
SCOPES_CSV = ",".join(SCOPES)

# Timeout for waiting on a chat.final response from OpenClaw.
RECEIVE_TIMEOUT_SEC = settings.openclaw_receive_timeout_sec
SKIP_EVENTS = {"health", "tick", "presence", "heartbeat"}


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _sign_connect(nonce: str, credentials: DeviceCredentials, gateway_token: str) -> dict:
    priv_bytes = _b64url_decode(credentials.private_key_b64)
    private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    signed_at_ms = int(time.time() * 1000)
    sign_payload = "|".join([
        "v2", credentials.device_id, CLIENT_ID, CLIENT_MODE,
        ROLE, SCOPES_CSV, str(signed_at_ms), gateway_token, nonce,
    ])
    sig_bytes = private_key.sign(sign_payload.encode())
    return {
        "device": {
            "id": credentials.device_id,
            "publicKey": credentials.public_key_b64,
            "signature": _b64url_encode(sig_bytes),
            "signedAt": signed_at_ms,
            "nonce": nonce,
        },
        "client": {
            "id": CLIENT_ID,
            "version": "clawmux-2.0",
            "platform": PLATFORM,
            "mode": CLIENT_MODE,
        },
        "role": ROLE,
        "scopes": SCOPES,
    }


class OpenClawConnectionError(Exception):
    pass


class OpenClawClient:
    """
    Persistent WebSocket client for a single OpenClaw instance.

    After connect(), a background _listen_loop() runs indefinitely.
    All chat.final events go through ClawAggregator which debounces the
    stream and picks the highest-seq valid message before routing it to:
      - The pending send_message() caller (via asyncio.Future)
      - The on_proactive callback (if no active request or post-timeout)
    """

    def __init__(
        self,
        instance_url: str,
        credentials: DeviceCredentials,
        on_proactive: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.instance_url = instance_url
        self.credentials = credentials
        self.gateway_token = credentials.gateway_token
        self._on_proactive = on_proactive

        self._ws: Optional[ClientConnection] = None
        self._connected = False
        self._listen_task: Optional[asyncio.Task] = None

        # One message in-flight at a time.
        self._send_lock = asyncio.Lock()
        self._active_msg_id: Optional[str] = None
        # Future resolves to (text, media_paths) tuple
        self._pending_future: Optional[asyncio.Future[tuple[str, list[str]]]] = None
        self._pending_future_msg_id: Optional[str] = None
        self._timed_out_msg_id: Optional[str] = None
        self._active_on_stream: Optional[Callable[[str], Awaitable[None]]] = None

        # Workaround for OpenClaw Gateway normalizer bug (strips zeroes like "13 480 000" -> "13 480 0")
        # We buffer the raw uncorrupted text from the 'agent' stream here.
        self._agent_texts: dict[str, str] = {}

        # Message aggregator: buffers chat.final events and emits the best one
        self._aggregator = ClawAggregator(debounce_ms=settings.claw_debounce_ms)
        self._aggregator.on_final = self._route_aggregated_message

        self._log = logger.bind(
            instance_url=instance_url,
            device_id=credentials.device_id[:12] + "...",
        )

    def _fire_and_log(self, coro: Awaitable, *, task_name: str = "task") -> asyncio.Task:
        """Schedule a coroutine as a background task with guaranteed error logging.

        Plain asyncio.create_task() swallows exceptions — they only surface as
        a cryptic 'Task exception was never retrieved' warning in stderr.
        This wrapper catches any exception and routes it to structlog.
        """
        async def _wrapper() -> None:
            try:
                await coro
            except Exception as exc:
                self._log.error("background_task_error", task=task_name, error=str(exc))

        return asyncio.create_task(_wrapper(), name=task_name)

    @property
    def is_connected(self) -> bool:
        return (
            self._connected
            and self._ws is not None
            and self._ws.protocol.state.name == "OPEN"
            and self._listen_task is not None
            and not self._listen_task.done()
        )

    async def connect(self) -> None:
        """
        Establish WS, authenticate, then start background _listen_loop().
        Raises OpenClawConnectionError on failure.
        """
        try:
            missing = []
            if not self.credentials.device_id.strip():
                missing.append("device_id")
            if not self.credentials.public_key_b64.strip():
                missing.append("public_key_b64")
            if not self.credentials.private_key_b64.strip():
                missing.append("private_key_b64")
            if not self.credentials.device_token.strip():
                missing.append("device_token")
            if not self.gateway_token.strip():
                missing.append("gateway_token")
            if missing:
                raise OpenClawConnectionError(
                    "Instance credentials are incomplete in DB "
                    f"(missing: {', '.join(missing)})."
                )

            self._log.info("connecting")
            origin = self.instance_url.replace("ws://", "http://").replace("/ws", "")
            if origin.startswith("http://openclaw:18789"):
                origin = "http://127.0.0.1:18789"
            self._ws = await websockets.connect(
                self.instance_url,
                additional_headers={"Origin": origin},
                open_timeout=10,
                close_timeout=5,
            )

            # Step 1: challenge
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            data = json.loads(raw)
            if data.get("event") != "connect.challenge":
                raise OpenClawConnectionError(
                    f"Expected connect.challenge, got: {data.get('event')}"
                )
            nonce = data["payload"]["nonce"]

            # Step 2: signed connect
            identity = _sign_connect(nonce, self.credentials, self.gateway_token)
            connect_msg = {
                "type": "req",
                "id": str(uuid.uuid4()),
                "method": "connect",
                "params": {
                    "minProtocol": 4,
                    "maxProtocol": 4,
                    "client": identity["client"],
                    "role": identity["role"],
                    "scopes": identity["scopes"],
                    "device": identity["device"],
                    "caps": ["tool-events"],
                    "auth": {
                        "token": self.gateway_token,
                        "deviceToken": self.credentials.device_token,
                    },
                },
            }
            await self._ws.send(json.dumps(connect_msg))

            # Step 3: verify response
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            res = json.loads(raw)
            if not res.get("ok"):
                raise OpenClawConnectionError(f"Connect rejected: {res.get('error', 'unknown')}")

            self._connected = True
            self._log.info("connected_ok")

            # Start persistent background listener
            self._listen_task = asyncio.create_task(
                self._listen_loop(),
                name=f"openclaw-listen-{self.credentials.device_id[:8]}",
            )

        except OpenClawConnectionError:
            raise
        except Exception as e:
            self._connected = False
            raise OpenClawConnectionError(f"Connection failed: {e}") from e

    async def send_message(
        self,
        message: str,
        session_key: str = "agent:main:main",
        on_stream: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> tuple[str, list[str]]:
        """
        Send a chat message. Returns a tuple of (response_text, media_paths).
        media_paths is a list of container-side file paths from mediaUrls (Route B).
        Blocks until chat.final arrives (or timeout).
        Thread-safe via _send_lock — one message at a time per client.
        """
        if not self.is_connected:
            raise OpenClawConnectionError("Not connected. Call connect() first.")

        async with self._send_lock:
            msg_id = str(uuid.uuid4())
            self._active_msg_id = msg_id
            # Fresh Future per request — prevents stale responses from a
            # previous timed-out call from being delivered to this call.
            self._pending_future = asyncio.get_running_loop().create_future()
            self._pending_future_msg_id = msg_id
            self._active_on_stream = on_stream
            self._log.info("sending_message", msg_id=msg_id, text_len=len(message))

            try:
                await self._ws.send(json.dumps({
                    "type": "req",
                    "id": msg_id,
                    "method": "chat.send",
                    "params": {
                        "sessionKey": session_key,
                        "message": message,
                        "deliver": True,
                        "idempotencyKey": str(uuid.uuid4()),
                    },
                }))

                # asyncio.shield so that TimeoutError cancellation
                # doesn't cancel the Future itself (listen_loop still owns it).
                result = await asyncio.wait_for(
                    asyncio.shield(self._pending_future),
                    timeout=RECEIVE_TIMEOUT_SEC,
                )
                text, media_paths = result
                self._log.info(
                    "response_received",
                    msg_id=msg_id,
                    response_len=len(text),
                    media_paths_count=len(media_paths),
                )
                return text, media_paths

            except asyncio.TimeoutError:
                self._log.warning("send_timeout", msg_id=msg_id)
                # Do NOT clear _pending_future here — leave it with the timed-out msg_id
                # so that _route_completed_message can detect this as a stale response
                # and deliver it via the proactive callback instead of losing it.
                # The future itself is cleared only in the finally block AFTER we've
                # recorded the timed-out msg_id for late-arrival detection.
                self._timed_out_msg_id = msg_id
                return "[Timeout: OpenClaw did not respond within the allotted time]", []
            except OpenClawConnectionError:
                # WS dropped mid-request (listen_loop set exception on the future).
                # Re-raise so ws_manager.send_message triggers reconnect + retry.
                self._log.warning("send_disconnect_mid_request", msg_id=msg_id)
                raise
            finally:
                self._active_msg_id = None
                self._pending_future = None
                self._pending_future_msg_id = None
                self._active_on_stream = None

    # ── Background listener ────────────────────────────────────────

    async def _listen_loop(self) -> None:
        """
        Reads all events from the OpenClaw WS indefinitely.
        Exits when the connection is closed or an unrecoverable error occurs.
        Signals ws_manager (via _connected=False) that reconnect is needed.

        If RAW_WS_DUMP=1 env var is set, every raw WS message is appended to
        /tmp/ws_raw_dump.jsonl (one JSON object per line, no truncation).
        Use `tail -f /tmp/ws_raw_dump.jsonl | python3 -m json.tool` to read.
        """
        dump_file = "/tmp/ws_raw_dump.jsonl" if os.getenv("RAW_WS_DUMP") == "1" else None
        self._log.info("listen_loop_started", raw_dump=dump_file or "disabled")
        try:
            async for raw in self._ws:
                try:
                    # Write full untruncated message to dump file if enabled
                    if dump_file:
                        try:
                            async with aiofiles.open(dump_file, "a") as f:
                                content = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
                                await f.write(content)
                                await f.write("\n")
                        except Exception:
                            pass
                    parsed = json.loads(raw)
                    await self._dispatch(parsed)
                except json.JSONDecodeError:
                    self._log.warning("invalid_json", raw=str(raw)[:100])
                except Exception as e:
                    self._log.error("dispatch_error", error=str(e))
        except asyncio.CancelledError:
            self._log.info("listen_loop_cancelled")
        except Exception as e:
            self._log.warning("listen_loop_error", error=str(e))
        finally:
            self._connected = False
            # Clear buffered agent texts to prevent memory leak on reconnect
            self._agent_texts.clear()
            self._log.info("listen_loop_ended")
            # Unblock any waiting send_message with an exception so
            # ws_manager catches it and triggers reconnect + retry.
            if self._pending_future is not None and not self._pending_future.done():
                self._pending_future.set_exception(
                    OpenClawConnectionError("WebSocket disconnected mid-request")
                )

    async def _dispatch(self, parsed: dict) -> None:
        """Route a single WS message to the right handler."""
        event = parsed.get("event", "")
        msg_type = parsed.get("type", "")

        # --- DEBUG: log everything including skipped events ---
        payload = parsed.get("payload", {})
        session_key = payload.get("sessionKey", "") if isinstance(payload, dict) else ""
        run_id = payload.get("runId", "") if isinstance(payload, dict) else ""
        
        # We MUST ignore events from subagent-owned sessions.
        # Subagents run in the background and are summarized by the main agent.
        # We only filter by sessionKey (where the event comes FROM), NOT by runId.
        # Announce events use a subagent runId but are routed to the main channel sessionKey —
        # they MUST pass through so the user receives the final answer from the main agent.
        if session_key and ":subagent:" in session_key:
            self._log.debug("ignored_subagent_event", ws_event=event, session_key=session_key, run_id=run_id)
            return


        state = payload.get("state", "") if isinstance(payload, dict) else ""
        self._log.debug(
            "ws_event_raw",
            ws_event=event or "(none)",
            msg_type=msg_type or "(none)",
            session_key=session_key or "(none)",
            state=state or "(none)",
            ok=parsed.get("ok"),
            skipped=(event in SKIP_EVENTS),
            full_payload=str(payload)[:1000],
        )

        if event in SKIP_EVENTS:
            return

        # Acknowledgement of chat.send (type=res)
        if msg_type == "res":
            if not parsed.get("ok"):
                error = parsed.get("error", "unknown")
                self._log.error("chat_send_rejected", error=error)
                if self._pending_future is not None and not self._pending_future.done():
                    self._pending_future.set_result((f"[Error: {error}]", []))
                self._active_msg_id = None
                self._pending_future = None
                self._pending_future_msg_id = None
            return

        # Track raw uncorrupted text from the background agent stream
        if event == "agent":
            payload_dict = payload if isinstance(payload, dict) else {}
            stream = payload_dict.get("stream", "")

            if stream == "assistant":
                # Buffer the clean, uncorrupted text (Gateway normalizer strips zeros)
                text = payload_dict.get("data", {}).get("text", "")
                run_id = payload_dict.get("runId", "")
                if text and run_id:
                    self._agent_texts[run_id] = text
                    # Trigger streaming directly from the pure agent stream 
                    # since chat stream is often missing for the final answer run.
                    if self._active_on_stream and self._active_msg_id:
                        self._fire_and_log(self._active_on_stream(text), task_name="on_stream_agent")

            elif stream == "lifecycle":
                phase = payload_dict.get("data", {}).get("phase", "")
                run_id = payload_dict.get("runId", "")

                if phase == "error" and self._active_msg_id:
                    error_msg = payload_dict.get("data", {}).get("error", "Unknown LLM error")
                    if self._pending_future and not self._pending_future.done():
                        self._log.error("agent_lifecycle_error", error=error_msg, msg_id=self._active_msg_id)
                        
                        # Preserve any text that was already generated before the crash
                        existing_text = self._agent_texts.get(run_id, "")
                        if existing_text:
                            final_text = f"{existing_text}\n\n⚠️ **Generation failure:** {error_msg}"
                        else:
                            final_text = f"[LLM error: {error_msg}]"
                            
                        self._pending_future.set_result((final_text, []))

                # GATEWAY BUG FALLBACK:
                # Sometimes OpenClaw sends agent:lifecycle:end but never sends chat.final
                if phase == "end" and run_id and self._active_msg_id:
                    agent_text = self._agent_texts.get(run_id, "")
                    if agent_text and self._pending_future and not self._pending_future.done():
                        self._log.warning(
                            "agent_lifecycle_end_fallback_scheduled",
                            run_id=run_id,
                            msg_id=self._active_msg_id,
                            text_len=len(agent_text),
                        )
                        _captured_run_id = run_id
                        _captured_msg_id = self._active_msg_id
                        _captured_text = agent_text

                        async def _delayed_fallback() -> None:
                            # Wait for a late chat.final before firing.
                            # chat.final normally arrives ~400ms after lifecycle:end.
                            # 450ms gives it a small grace window.
                            await asyncio.sleep(0.45)
                            if (
                                self._pending_future is not None
                                and not self._pending_future.done()
                                and self._pending_future_msg_id == _captured_msg_id
                            ):
                                self._log.warning(
                                    "agent_lifecycle_end_fallback_fired",
                                    msg_id=_captured_msg_id,
                                    text_len=len(_captured_text),
                                )
        # lifecycle:end fallback: resolve Future DIRECTLY with empty media_paths
                                self._pending_future.set_result((_captured_text, []))

                        asyncio.create_task(
                            _delayed_fallback(),
                            name=f"lifecycle-fallback-{run_id[:8]}",
                        )
            return


        # chat.final / chat.partial → feed into aggregator
        if event == "chat":
            payload_dict = payload if isinstance(payload, dict) else {}
            text = self._extract_text(payload_dict)
            seq = payload_dict.get("seq", 0)
            msg_id = self._active_msg_id or "proactive"
            run_id = payload_dict.get("runId", "")

            # OPENCLAW BUG WORKAROUND:
            # The Gateway's normalized chat stream has a bug where it strips zeroes.
            # Additionally, it injects internal reasoning steps which artificially inflates length.
            # We override the corrupted text with the raw, clean text if available.
            agent_text = self._agent_texts.get(run_id)
            if agent_text:
                text = agent_text

            # Fire streaming callback for partial updates
            if state in ("partial", "delta") and self._active_on_stream and self._active_msg_id:
                if text:
                    self._fire_and_log(self._active_on_stream(text), task_name="on_stream_chat")

            # Intercept outbound media paths from chat.final (Route B: OpenClaw → Mattermost).
            # The agent sets mediaUrl/mediaUrls in its reply when it generates a file.
            # We pass these paths to the router via ClawMessage.media_paths so they can
            # be uploaded to Mattermost and attached to the reply post.
            media_paths: list[str] = []
            if state == "final":
                # 1. Try explicit mediaUrls (if gateway supports it)
                raw_urls = payload_dict.get("mediaUrls")
                if isinstance(raw_urls, list):
                    media_paths = [p for p in raw_urls if isinstance(p, str) and p.strip()]
                single_url = payload_dict.get("mediaUrl", "")
                if isinstance(single_url, str) and single_url.strip():
                    if single_url.strip() not in media_paths:
                        media_paths.append(single_url.strip())
                
                # 2. Extract paths directly from the text (markdown links or raw paths)
                # Matches: [Link](/home/node/.openclaw/...) or just /home/node/.openclaw/...
                openclaw_prefix = "/home/node/.openclaw/"
                path_pattern = re.compile(rf"({openclaw_prefix}[^\s\"\'\)]+)")
                for match in path_pattern.finditer(text):
                    extracted_path = match.group(1).strip()
                    if extracted_path not in media_paths:
                        media_paths.append(extracted_path)
                
                # Remove internal links and paths from the text so they don't show up in chat
                markdown_pattern = re.compile(rf"\[[^\]]*\]\({openclaw_prefix}[^\)]+\)")
                text = markdown_pattern.sub("", text)
                
                raw_pattern = re.compile(rf"{openclaw_prefix}[^\s\"\'\)]+")
                text = raw_pattern.sub("", text)
                
                text = text.strip()

                if media_paths:
                    self._log.info(
                        "chat_final_media_intercepted",
                        media_paths=media_paths,
                        msg_id=msg_id,
                    )

            # Always log for observability; aggregator will filter non-finals
            self._log.info(
                "chat_event_received",
                session_key=session_key or "(none)",
                state=state or "(none)",
                seq=seq,
                text_len=len(text),
                text_preview=text[:200] if text else "(empty)",
                has_pending_future=self._pending_future is not None,
                active_msg_id=self._active_msg_id,
            )
            # Route through aggregator — it will call _route_aggregated_message
            # once the debounce window expires with the best final message.
            msg = ClawMessage(
                msg_id=msg_id,
                seq=seq,
                text=text,
                state=state,
                ts=time.time(),
                media_paths=media_paths,
            )
            await self._aggregator.add_event(msg)

            # Cleanup memory after final
            if state == "final" and run_id in self._agent_texts:
                asyncio.get_running_loop().call_later(10.0, self._agent_texts.pop, run_id, None)

            return

        # cron event — lifecycle notifications from OpenClaw scheduler
        # payload.summary contains the user-facing notification text.
        #
        # OpenClaw cron lifecycle:
        #   started  → no summary yet, skip
        #   finished → summary IS here (the reminder text), must deliver!
        #   removed  → cleanup only, no summary, skip
        if event == "cron":
            action = payload.get("action", "") if isinstance(payload, dict) else ""
            summary = payload.get("summary", "") if isinstance(payload, dict) else ""
            delivery_status = payload.get("deliveryStatus", "") if isinstance(payload, dict) else ""

            # Always skip 'removed' (pure cleanup, no content)
            # Skip 'started' implicitly (no summary)
            # Skip 'finished' only if there is no summary to deliver
            if action == "removed" or (not summary):
                self._log.debug("cron_job_lifecycle_skip", action=action, job_id=payload.get("jobId"))
                return

            self._log.info(
                "cron_event",
                job_id=payload.get("jobId", ""),
                action=action,
                summary_preview=summary[:100],
                delivery_status=delivery_status,
            )
            # Deliver to user if there is a human-readable summary
            self._fire_and_log(self._on_proactive(summary), task_name="cron_proactive")
            return

        # Legacy session.updated (kept for compatibility)
        if event == "session.updated":
            self._log.debug("legacy_session_updated")

    async def _route_aggregated_message(self, msg: ClawMessage) -> None:
        """
        Called by ClawAggregator after the debounce window with the best final
        ClawMessage for a given msg_id.

        Routes to:
          - The pending send_message() Future if msg_id matches and it's not
            timed-out yet.
          - The on_proactive callback for proactive or post-timeout responses.
        """
        timed_out_msg_id = self._timed_out_msg_id

        if (
            self._pending_future is not None
            and not self._pending_future.done()
            and self._pending_future_msg_id == msg.msg_id
            and timed_out_msg_id != self._pending_future_msg_id
        ):
            self._log.info(
                "routing_aggregated_to_request",
                msg_id=msg.msg_id,
                seq=msg.seq,
                text_len=len(msg.text),
                media_paths_count=len(msg.media_paths),
            )
            self._pending_future.set_result((msg.text, msg.media_paths))
        else:
            # No active request, mismatched msg_id, or late post-timeout response.
            reason = "late_timeout_response" if timed_out_msg_id == msg.msg_id else "proactive_unsolicited"
            self._log.info(
                reason,
                msg_id=msg.msg_id,
                seq=msg.seq,
                text_len=len(msg.text),
            )
            if timed_out_msg_id == msg.msg_id:
                self._timed_out_msg_id = None  # clear after handling
            if self._on_proactive and msg.text:
                self._fire_and_log(self._on_proactive(msg.text), task_name="proactive_unsolicited")

    @staticmethod
    def _extract_text(payload: dict) -> str:
        """Extract plain text from a chat.final payload.

        Tries multiple payload shapes to handle OpenClaw version differences:
          1. payload.message.content[{type:text, text:...}]  (standard)
          2. payload.text                                     (flat fallback)
        """
        # Primary: structured message.content
        message_obj = payload.get("message", {}) if isinstance(payload, dict) else {}
        content = message_obj.get("content", [])
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        text_parts.append(text)
            if text_parts:
                return "\n\n".join(text_parts)
        
        # Fallback: payload.text directly
        flat_text = payload.get("text", "") if isinstance(payload, dict) else ""
        if flat_text:
            return flat_text
        return ""

    async def close(self) -> None:
        """Close WS connection, cancel aggregator timers, and cancel listen task."""
        self._connected = False
        self._aggregator.cancel_all()
        # Clear buffered agent texts to prevent memory leak
        self._agent_texts.clear()
        if self._listen_task is not None and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._log.info("connection_closed")
