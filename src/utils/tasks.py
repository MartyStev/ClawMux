"""
Strong-reference fire-and-forget tasks.

Plain ``asyncio.create_task(coro)`` without keeping the returned reference is
unsafe: the event loop only holds a weak reference, so the task can be garbage
collected mid-execution, and any exception dies silently.
"""

import asyncio
from collections.abc import Awaitable, Coroutine
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Module-level registry — prevents GC of in-flight background tasks.
_background_tasks: set[asyncio.Task] = set()


def fire_and_forget(coro: Awaitable[Any], *, name: str | None = None) -> asyncio.Task:
    """Schedule *coro* as a background task, log its error on failure."""
    if isinstance(coro, Coroutine):
        task = asyncio.create_task(coro, name=name)
    else:

        async def _wrap() -> Any:
            return await coro

        task = asyncio.create_task(_wrap(), name=name)
    _background_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error(
                "background_task_error",
                task=name or t.get_name(),
                error=str(exc),
                exc_info=exc,
            )

    task.add_done_callback(_done)
    return task
