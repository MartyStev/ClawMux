"""
ClawMux — Configuration.

All settings are loaded from environment variables (or an .env file).
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Database ──────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://router:router@localhost:5432/ws_router",
        description="Async PostgreSQL connection string",
    )

    # ── Mattermost ────────────────────────────────────────────────
    mattermost_url: str = Field(
        default="https://mattermost.example.com",
        description="Mattermost server URL (without trailing slash)",
    )
    mattermost_token: str = Field(
        default="",
        description="Mattermost bot token for API & WebSocket access",
    )
    mattermost_bot_username: str = Field(
        default="openclaw",
        description="Bot username to ignore own messages",
    )

    # ── WS Connection Manager ─────────────────────────────────────
    ws_idle_timeout_sec: int = Field(
        default=1800,
        description="Close WS to OpenClaw after N seconds of inactivity (default: 30 min)",
    )
    ws_cleanup_interval_sec: int = Field(
        default=300,
        description="How often to check for idle connections (default: 5 min)",
    )
    ws_reconnect_max_retries: int = Field(
        default=3,
        description="Max reconnect attempts before giving up",
    )
    ws_reconnect_base_delay_sec: float = Field(
        default=1.0,
        description="Base delay for exponential backoff on reconnect",
    )
    openclaw_receive_timeout_sec: int = Field(
        default=300,
        description="Timeout for waiting on a chat.final response from OpenClaw (seconds)",
    )
    claw_debounce_ms: int = Field(
        default=150,
        description="Debounce window (ms) for ClawAggregator — wait this long after last chat.final before picking the best message",
    )

    # ── Attachments ───────────────────────────────────────────────────────────────
    workspace_base_path: str = Field(
        default="/configs",
        description=(
            "Base path inside ws_router container where openclaw configs are mounted. "
            "Structure: <base>/<uuid>/workspace/..."
        ),
    )
    attachment_max_size_mb: int = Field(
        default=50,
        description="Max file size in MB for download/upload. Files over this limit are skipped.",
    )
    container_workspace_root: str = Field(
        default="/home/node/.openclaw/workspace",
        description="Workspace root path inside the OpenClaw container.",
    )

    # ── Control-Plane API ─────────────────────────────────────────────────────────
    api_token: str = Field(
        default="",
        description="Secret token for POST /api/v1/trigger (X-Api-Token header). Empty = endpoint disabled.",
    )
    mm_action_proxy_url: str = Field(
        default="http://tools-server:3000/mm/action",
        description=(
            "Internal endpoint used by /api/v1/mm/action proxy for Mattermost "
            "interactive buttons."
        ),
    )

    # ── Dify Fallback Bot ─────────────────────────────────────────────────────────
    dify_base_url: str = Field(
        default="http://localhost:8081/v1",
        description="Dify Chat API base URL (without trailing slash).",
    )
    dify_api_key: str = Field(
        default="",
        description=(
            "Dify application API key (Bearer token). "
            "When set, users without an OpenClaw instance are routed to Dify instead of "
            "receiving an error message."
        ),
    )
    dify_timeout_sec: int = Field(
        default=120,
        description="Max seconds to wait for a full Dify streaming response.",
    )

    # ── Server ────────────────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0", description="Bind host")
    port: int = Field(default=8060, description="Bind port")
    log_level: str = Field(default="INFO", description="Logging level")

    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}


settings = Settings()
