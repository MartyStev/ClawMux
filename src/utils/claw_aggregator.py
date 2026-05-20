"""
ClawMux — OpenClaw Message Aggregator.

Problem:
    OpenClaw sends multiple chat.final events for a single agent run.
    The first one (low seq, e.g. seq=2) is an early/intermediate result,
    while the real answer arrives later (seq=150+). Without buffering,
    the router resolves the pending future with the *first* final, causing
    Mattermost to show a truncated/placeholder response.

Solution:
    ClawAggregator buffers all chat.final events per msg_id and uses a
    debounce timer (DEBOUNCE_MS) to wait for the stream to settle.
    After the timer fires, _select_best() picks the final message with:
      1. state == "final"
      2. non-trivial text (len > 5, not a placeholder like "Ok", "thinking...")
      3. highest seq number

Architecture:
    OpenClaw WS
        ↓
    Event Handler (_dispatch)
        ↓
    ClawAggregator.add_event()   ← buffers + resets debounce timer
        ↓  (after DEBOUNCE_MS silence)
    on_final(ClawMessage)        ← callback set by OpenClawClient
        ↓
    _route_aggregated_message()  → pending future OR proactive
"""

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# Short exact strings that are obviously placeholder-only responses.
# Must be EXACT short tokens — do NOT put long prefixes here, or you'll
# filter real answers like "Ok, here are the details..." or "I'll check now..."
_JUNK_EXACT = frozenset({
    "ok", "ok.",
    "thinking", "thinking...",
    "...", "…",
})

# Only suppress when the ENTIRE text (stripped, lowered) IS one of these tokens.
def is_valid_text(text: str) -> bool:
    """Return True if text is a real, non-placeholder response."""
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < 5:
        return False
    if stripped.lower() in _JUNK_EXACT:
        return False
    return True


@dataclass
class ClawMessage:
    """Represents a single chat.final event received from OpenClaw."""

    msg_id: str          # Client-generated request UUID (chat.send id)
    seq: int             # Sequence number from OpenClaw payload
    text: str            # Extracted plain text
    state: str           # "final" | "partial" | etc.
    ts: float            # Unix timestamp when received (time.time())
    media_paths: list    # Container-side paths from mediaUrls/mediaUrl (Route B)


class ClawAggregator:
    """
    Buffers chat.final events per msg_id and emits the *best* one
    after a debounce window of silence.

    Usage:
        aggregator = ClawAggregator(debounce_ms=400)
        aggregator.on_final = my_async_callback   # set before use

        # In event handler:
        await aggregator.add_event(msg)

    The on_final callback receives the single best ClawMessage.
    """

    def __init__(self, debounce_ms: int = 400) -> None:
        self.debounce_ms = debounce_ms
        self.on_final: Optional[Callable[[ClawMessage], Awaitable[None]]] = None

        # Per msg_id buffers and debounce tasks
        self._buffers: Dict[str, List[ClawMessage]] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

        self._log = logger.bind(component="ClawAggregator")

    async def add_event(self, msg: ClawMessage) -> None:
        """
        Register a new chat event. Resets the debounce timer for this msg_id.
        Only state=="final" events are buffered; others are logged and dropped.
        """
        if msg.state != "final":
            self._log.debug(
                "aggregator_skip_non_final",
                msg_id=msg.msg_id,
                seq=msg.seq,
                state=msg.state,
            )
            return

        self._log.info(
            "claw_event",
            msg_id=msg.msg_id,
            seq=msg.seq,
            state=msg.state,
            text_preview=msg.text[:80] if msg.text else "(empty)",
        )

        buf = self._buffers.setdefault(msg.msg_id, [])
        buf.append(msg)

        # Cancel existing debounce timer for this msg_id and restart
        existing = self._tasks.get(msg.msg_id)
        if existing and not existing.done():
            existing.cancel()

        self._tasks[msg.msg_id] = asyncio.create_task(
            self._finalize_later(msg.msg_id),
            name=f"aggregator-debounce-{msg.msg_id[:8]}",
        )

    async def _finalize_later(self, msg_id: str) -> None:
        """Wait for the debounce window to expire, then pick the best message."""
        try:
            await asyncio.sleep(self.debounce_ms / 1000.0)
        except asyncio.CancelledError:
            # Timer was reset because a new event arrived — that's expected.
            return

        messages = self._buffers.pop(msg_id, [])
        self._tasks.pop(msg_id, None)

        if not messages:
            self._log.warning("aggregator_empty_buffer", msg_id=msg_id)
            return

        final_msg = self._select_best(messages)

        if final_msg is None:
            self._log.warning(
                "aggregator_no_valid_final",
                msg_id=msg_id,
                total_events=len(messages),
                seqs=[m.seq for m in messages],
            )
            return

        import time as _time
        aggregation_latency_ms = round((_time.time() - min(m.ts for m in messages)) * 1000)
        self._log.info(
            "claw_selected_final",
            msg_id=msg_id,
            seq=final_msg.seq,
            text_len=len(final_msg.text),
            total_candidates=len(messages),
            all_seqs=[m.seq for m in messages],
            aggregation_latency_ms=aggregation_latency_ms,
        )

        if self.on_final is None:
            self._log.error("aggregator_no_on_final_callback", msg_id=msg_id)
            return

        # Wrap in try/except — an unhandled exception here would silently kill
        # the background task and leave the pending Future unresolved forever.
        try:
            await self.on_final(final_msg)
        except Exception as exc:
            self._log.error(
                "aggregator_on_final_error",
                msg_id=msg_id,
                error=str(exc),
                exc_info=True,
            )

    def _select_best(self, messages: List[ClawMessage]) -> Optional[ClawMessage]:
        """
        Pick the best final message from the buffer:
          1. Filter to state == "final"
          2. Prefer non-trivial text (is_valid_text) with highest seq
          3. Fallback: if ALL are junk/empty, take highest seq anyway
             (better to send something than nothing)
        """
        finals = [m for m in messages if m.state == "final"]
        if not finals:
            return None

        valid = [m for m in finals if is_valid_text(m.text)]
        if valid:
            return max(valid, key=lambda m: m.seq)

        # Fallback: all candidates looked like junk/placeholder but we must
        # not silently drop — pick highest-seq among non-empty ones.
        non_empty = [m for m in finals if m.text and m.text.strip()]
        if non_empty:
            best = max(non_empty, key=lambda m: m.seq)
            self._log.warning(
                "aggregator_fallback_to_nonempty",
                seqs=[m.seq for m in finals],
                chosen_seq=best.seq,
                preview=best.text[:80],
            )
            return best

        self._log.warning(
            "aggregator_all_finals_empty",
            seqs=[m.seq for m in finals],
        )
        return None

    def cancel_all(self) -> None:
        """Cancel all pending debounce timers (call on shutdown)."""
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self._buffers.clear()
