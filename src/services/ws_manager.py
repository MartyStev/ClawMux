"""
ClawMux — WebSocket Connection Manager.

Manages persistent WS connections to OpenClaw instances.

Key changes vs v1:
  - Connections are PERSISTENT — never closed on idle (proactive messages need live WS)
  - Per-user guardian task monitors listen_loop health and auto-reconnects
  - Accepts on_proactive(user_id, text) callback for proactive message delivery
  - Stores InstanceInfo per user for reconnection without user interaction
"""

import asyncio
import time
from typing import Awaitable, Callable, Dict, Optional

import structlog

from src.core.config import settings
from src.services.mapping import DeviceCredentials, InstanceInfo
from src.utils.metrics import ws_active_connections, ws_errors_total
from src.services.openclaw_client import OpenClawClient, OpenClawConnectionError

logger = structlog.get_logger(__name__)


class WSConnectionManager:
    """
    Connection pool for OpenClaw WebSocket clients.

    - 1 user = 1 persistent WS connection with background listen loop
    - Per-user guardian task auto-reconnects on disconnect
    - Proactive messages from OpenClaw are forwarded via on_proactive callback
    """

    def __init__(
        self,
        on_proactive: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ):
        """
        Args:
            on_proactive: Called when OpenClaw sends a proactive message.
                          Signature: async def on_proactive(user_id: str, text: str)
        """
        self._on_proactive = on_proactive
        self._clients: Dict[str, OpenClawClient] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._user_info: Dict[str, InstanceInfo] = {}  # stored for reconnect
        self._guardian_tasks: Dict[str, asyncio.Task] = {}
        self._last_active: Dict[str, float] = {}  # user_id → last message timestamp
        self._shutting_down = False

        # Start periodic idle-connection cleanup
        self._cleanup_task: asyncio.Task = asyncio.get_event_loop().create_task(
            self._cleanup_loop(),
            name="ws-idle-cleanup",
        )

    def _get_lock(self, user_id: str) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    def get_cached_info(self, user_id: str) -> Optional[InstanceInfo]:
        """Return cached InstanceInfo if we already have a connection for this user."""
        return self._user_info.get(user_id)

    def set_proactive_handler(self, handler: Callable[[str, str], Awaitable[None]]) -> None:
        self._on_proactive = handler

    def _make_proactive_cb(self, user_id: str) -> Callable[[str], Awaitable[None]]:
        """Create a proactive callback bound to a specific user_id."""
        async def _cb(text: str) -> None:
            if self._on_proactive:
                await self._on_proactive(user_id, text)
        return _cb

    async def get_or_create(self, user_id: str, info: InstanceInfo) -> OpenClawClient:
        """
        Return an existing connected client or create a new one.
        Thread-safe via per-user asyncio.Lock.
        """
        lock = self._get_lock(user_id)
        async with lock:
            client = self._clients.get(user_id)

            if client is not None and client.is_connected:
                logger.debug("connection_reused", user_id=user_id)
                return client

            if client is not None:
                logger.info("closing_stale_connection", user_id=user_id)
                await client.close()

            new_client = OpenClawClient(
                instance_url=info.instance_url,
                credentials=info.credentials,
                on_proactive=self._make_proactive_cb(user_id),
            )
            await self._connect_with_retry(user_id, new_client)

            self._clients[user_id] = new_client
            self._user_info[user_id] = info
            ws_active_connections.set(len(self._clients))

            # Start/restart guardian for this user
            self._start_guardian(user_id)

            return new_client

    async def _connect_with_retry(self, user_id: str, client: OpenClawClient) -> None:
        max_retries = settings.ws_reconnect_max_retries
        base_delay = settings.ws_reconnect_base_delay_sec
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                await client.connect()
                logger.info("connection_established", user_id=user_id, attempt=attempt)
                return
            except OpenClawConnectionError as e:
                last_error = e
                if attempt < max_retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "connection_retry",
                        user_id=user_id,
                        attempt=attempt,
                        max_retries=max_retries,
                        delay_sec=delay,
                        error=str(e),
                    )
                    await asyncio.sleep(delay)

        logger.error("connection_failed_all_retries", user_id=user_id, error=str(last_error))
        ws_errors_total.labels(error_type="connect_failed").inc()
        raise OpenClawConnectionError(
            f"Failed to connect after {max_retries} attempts: {last_error}"
        )

    def _start_guardian(self, user_id: str) -> None:
        """Start (or restart) the guardian task for a user."""
        existing = self._guardian_tasks.get(user_id)
        if existing is not None and not existing.done():
            existing.cancel()

        self._guardian_tasks[user_id] = asyncio.create_task(
            self._guardian_loop(user_id),
            name=f"guardian-{user_id[:8]}",
        )

    async def _guardian_loop(self, user_id: str) -> None:
        """
        Monitor connection health and reconnect on disconnect.
        Runs indefinitely until shutdown or user is removed.
        """
        logger.info("guardian_started", user_id=user_id)

        while not self._shutting_down:
            client = self._clients.get(user_id)
            if client is None:
                break

            listen_task = client._listen_task
            if listen_task is not None and not listen_task.done():
                try:
                    # Block until listen_loop exits (= disconnected)
                    await asyncio.wait_for(asyncio.shield(listen_task), timeout=60)
                except asyncio.TimeoutError:
                    # Still alive — loop back and wait more
                    continue
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass

            if self._shutting_down:
                break

            logger.info("guardian_detected_disconnect", user_id=user_id)
            await asyncio.sleep(5)  # brief pause before reconnect

            try:
                info = self._user_info.get(user_id)
                if info is None:
                    logger.warning("guardian_no_info", user_id=user_id)
                    break

                lock = self._get_lock(user_id)
                async with lock:
                    old_client = self._clients.get(user_id)
                    if old_client:
                        await old_client.close()

                    new_client = OpenClawClient(
                        instance_url=info.instance_url,
                        credentials=info.credentials,
                        on_proactive=self._make_proactive_cb(user_id),
                    )
                    await self._connect_with_retry(user_id, new_client)
                    self._clients[user_id] = new_client
                    logger.info("guardian_reconnected", user_id=user_id)

            except Exception as e:
                logger.error("guardian_reconnect_failed", user_id=user_id, error=str(e))
                await asyncio.sleep(10)

        logger.info("guardian_stopped", user_id=user_id)

    async def send_message(
        self,
        user_id: str,
        info: InstanceInfo,
        message: str,
        session_key: str = "agent:main:main",
        on_stream: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> tuple[str, list[str]]:
        """
        Send a message to user's OpenClaw instance and get the response.
        Returns a tuple of (response_text, media_paths).
        Handles reconnection if the WS drops mid-flight.
        """
        self._last_active[user_id] = time.time()  # track activity for idle cleanup
        client = await self.get_or_create(user_id, info)

        try:
            return await client.send_message(message, session_key=session_key, on_stream=on_stream)
        except (OpenClawConnectionError, Exception) as e:
            logger.warning("send_failed_reconnecting", user_id=user_id, error=str(e))
            await client.close()
            client = await self.get_or_create(user_id, info)
            return await client.send_message(message, session_key=session_key, on_stream=on_stream)

    async def close_connection(self, user_id: str) -> None:
        """Close and remove connection for a specific user."""
        # Stop guardian first
        guardian = self._guardian_tasks.pop(user_id, None)
        if guardian is not None and not guardian.done():
            guardian.cancel()
            try:
                await guardian
            except asyncio.CancelledError:
                pass

        lock = self._get_lock(user_id)
        async with lock:
            client = self._clients.pop(user_id, None)
            self._user_info.pop(user_id, None)
            if client is not None:
                await client.close()
                ws_active_connections.set(len(self._clients))
                logger.info("connection_removed", user_id=user_id)
            self._locks.pop(user_id, None)

    @property
    def active_count(self) -> int:
        return len(self._clients)

    async def _cleanup_loop(self) -> None:
        """Periodically close connections idle longer than ws_idle_timeout_sec.

        Idle = no send_message() call in the last N seconds.
        Proactive connections (cron reminders) keep their own WS alive via the
        guardian loop, so closing idle ones here is safe — guardian will
        reconnect on next user message.
        """
        idle_timeout = settings.ws_idle_timeout_sec
        interval = settings.ws_cleanup_interval_sec
        logger.info("cleanup_loop_started", idle_timeout_sec=idle_timeout, interval_sec=interval)

        while not self._shutting_down:
            await asyncio.sleep(interval)
            if self._shutting_down:
                break

            now = time.time()
            idle_users = [
                uid for uid, last in list(self._last_active.items())
                if (now - last) > idle_timeout and uid in self._clients
            ]

            for user_id in idle_users:
                logger.info("closing_idle_connection", user_id=user_id, idle_sec=round(now - self._last_active[user_id]))
                self._last_active.pop(user_id, None)
                await self.close_connection(user_id)

        logger.info("cleanup_loop_stopped")

    async def close_all(self) -> None:
        """Close all connections (for graceful shutdown)."""
        self._shutting_down = True

        # Cancel cleanup loop
        if not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Cancel all guardians
        guardians = list(self._guardian_tasks.values())
        for g in guardians:
            g.cancel()
        if guardians:
            await asyncio.gather(*guardians, return_exceptions=True)
        self._guardian_tasks.clear()

        # Close all clients
        user_ids = list(self._clients.keys())
        for user_id in user_ids:
            client = self._clients.pop(user_id, None)
            if client:
                await client.close()

        self._locks.clear()
        logger.info("all_connections_closed", count=len(user_ids))
