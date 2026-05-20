"""
ClawMux — Prometheus Metrics.
"""

from prometheus_client import Counter, Gauge, Histogram

# ── Gauges ───────────────────────────────────────────────────────
ws_active_connections = Gauge(
    "ws_router_active_connections",
    "Number of active WebSocket connections to OpenClaw instances",
)

# ── Counters ─────────────────────────────────────────────────────
messages_total = Counter(
    "ws_router_messages_total",
    "Total messages processed",
    ["status"],  # success, error, unmapped
)

ws_errors_total = Counter(
    "ws_router_ws_errors_total",
    "Total WebSocket errors",
    ["error_type"],  # connect_failed, send_failed, timeout
)

mattermost_errors_total = Counter(
    "ws_router_mattermost_errors_total",
    "Total Mattermost API errors",
    ["error_type"],  # ws_disconnect, send_failed
)

# ── Histograms ───────────────────────────────────────────────────
request_duration = Histogram(
    "ws_router_request_duration_seconds",
    "End-to-end latency: Mattermost message received → reply sent",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)
