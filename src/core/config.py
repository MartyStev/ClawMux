"""
ClawMux — Configuration.

All settings are loaded from environment variables (or an .env file).
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Database ──────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://router:router@localhost:5432/ws_router",
        description="Async PostgreSQL connection string",
    )

    # ── Mattermost ────────────────────────────────────────────────
    enable_mattermost: bool = Field(
        default=True,
        description="Enable Mattermost channel adapter",
    )
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

    # ── Telegram ──────────────────────────────────────────────────
    enable_telegram: bool = Field(
        default=False,
        description="Enable Telegram Bot channel adapter",
    )
    telegram_bot_token: str = Field(
        default="",
        description="Telegram bot token from @BotFather",
    )

    # ── Bitrix24 ──────────────────────────────────────────────────
    enable_bitrix: bool = Field(
        default=False,
        description="Enable Bitrix24 chat bot adapter",
    )
    bitrix_webhook_url: str = Field(
        default="",
        description="Bitrix24 incoming webhook URL for REST API calls (e.g. https://portal.bitrix24.com/rest/1/token/)",
    )
    bitrix_bot_id: int = Field(
        default=0,
        description="Bitrix24 Bot ID registered on the portal",
    )
    bitrix_inbound_secret: str = Field(
        default="",
        description=(
            "Bitrix24 incoming webhook token. Inbound events must present it via "
            "auth.access_token or ?secure=/?access_token= query. Empty = webhook disabled."
        ),
    )

    # ── Slack ─────────────────────────────────────────────────────
    enable_slack: bool = Field(
        default=False,
        description="Enable Slack channel adapter (Socket Mode)",
    )
    slack_bot_token: str = Field(
        default="",
        description="Slack Bot User OAuth Token (xoxb-...)",
    )
    slack_app_token: str = Field(
        default="",
        description="Slack App-level Token for Socket Mode (xapp-...)",
    )

    # ── VK Teams (Myteam) ─────────────────────────────────────────
    enable_vk_teams: bool = Field(
        default=False,
        description="Enable VK Teams (Myteam) channel adapter",
    )
    vk_teams_bot_token: str = Field(
        default="",
        description="VK Teams Bot API Token",
    )
    vk_teams_api_url: str = Field(
        default="https://myteam.mail.ru/bot/v1",
        description="VK Teams Bot API base URL",
    )

    # ── Microsoft Teams ───────────────────────────────────────────
    enable_teams: bool = Field(
        default=False,
        description="Enable Microsoft Teams Bot adapter",
    )
    teams_app_id: str = Field(
        default="",
        description="Microsoft Azure Bot App ID",
    )
    teams_app_password: str = Field(
        default="",
        description="Microsoft Azure Bot App Password / Secret",
    )
    teams_jwt_audience: str = Field(
        default="",
        description=(
            "Expected 'aud' claim of inbound Bot Framework JWTs. "
            "Empty = use TEAMS_APP_ID. Webhook is rejected when neither is set."
        ),
    )
    teams_jwks_urls: str = Field(
        default=(
            "https://login.microsoftonline.com/common/discovery/keys,"
            "https://login.microsoftonline.com/common/discovery/v2.0/keys"
        ),
        description="Comma-separated JWKS URLs used to verify inbound Teams tokens",
    )
    teams_allowed_issuers: str = Field(
        default=(
            "https://api.botframework.com,"
            "https://sts.windows.net/d6d49420-f39b-4df7-a1dc-d59a935871db/,"
            "https://login.microsoftonline.com/d6d49420-f39b-4df7-a1dc-d59a935871db/v2.0"
        ),
        description="Comma-separated accepted JWT issuers for inbound Teams activities",
    )
    teams_allowed_service_url_hosts: str = Field(
        default="smba.trafficmanager.net,smtg.trafficmanager.net",
        description=(
            "Comma-separated host suffix whitelist for activity serviceUrl. "
            "Bearer tokens are only ever sent to whitelisted HTTPS hosts (anti-SSRF)."
        ),
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
    mapping_cache_ttl_sec: int = Field(
        default=300,
        description=(
            "TTL of the in-process mapping caches. Entries are also version-checked "
            "against mapping_state in the DB, so mutations in any replica are "
            "visible immediately; TTL only bounds staleness of external DB edits."
        ),
    )
    claw_debounce_ms: int = Field(
        default=150,
        description="Debounce window (ms) for ClawAggregator — wait this long after last chat.final before picking the best message",
    )
    claw_min_valid_text_len: int = Field(
        default=5,
        description=(
            "Minimum length of an aggregated answer that counts as a real reply "
            "(shorter texts are treated as placeholders unless nothing better arrives)."
        ),
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
    workspace_template_path: str = Field(
        default="./templates/default_workspace",
        description="Path to default workspace template directory containing AGENTS.md, openclaw.json, subagents, and mcp configs.",
    )

    # ── Auto-Provisioning ─────────────────────────────────────────
    enable_auto_provisioning: bool = Field(
        default=False,
        description="Automatically provision an OpenClaw instance on first message from unmapped user",
    )
    provisioning_driver: str = Field(
        default="webhook",
        description="Driver to use for provisioning: 'webhook' or 'mock'",
    )
    provisioning_webhook_url: str = Field(
        default="",
        description="External orchestrator webhook URL (e.g. http://orchestrator:8000/api/v1/instances/provision)",
    )
    provisioning_webhook_token: str = Field(
        default="",
        description="Bearer/API token for external provisioning webhook",
    )
    provisioning_timeout_sec: int = Field(
        default=60,
        description="Maximum seconds to wait for instance auto-provisioning and readiness",
    )

    # ── Control-Plane API ─────────────────────────────────────────────────────────
    api_token: str = Field(
        default="",
        description="Secret token for POST /api/v1/trigger (X-Api-Token header). Empty = endpoint disabled.",
    )
    mm_action_proxy_url: str = Field(
        default="http://tools-server:3000/mm/action",
        description=("Internal endpoint used by /api/v1/mm/action proxy for Mattermost interactive buttons."),
    )
    mm_action_shared_secret: str = Field(
        default="",
        description=(
            "Shared secret for POST /api/v1/mm/action (X-MM-Action-Secret header "
            "or ?secret= query param, e.g. in the registered callback URL). "
            "Empty = endpoint disabled."
        ),
    )

    # ── Credential Encryption ────────────────────────────────────────────────
    credential_encryption_key: str = Field(
        default="",
        description=(
            "Fernet key (base64, 44 chars) used to encrypt OpenClaw private keys "
            "and tokens at rest in PostgreSQL. Generate: "
            "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'. "
            "Empty = stored credentials remain plaintext (not recommended)."
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
