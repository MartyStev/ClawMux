# Changelog

All notable changes to ClawMux will be documented in this file.

## [Unreleased]

### Security
- **Workspace path traversal closed**: `container_path_to_host()` now rejects
  any agent-controlled path (OpenClaw `mediaUrl(s)`) that normalizes outside
  `WORKSPACE_BASE_PATH/<uuid>/` or resolves through a symlink to escape the
  tenant directory — previously `../` segments could exfiltrate other users'
  workspaces or host files into chat.
- **Provisioning response validated**: `instance_uuid` returned by the
  provisioning webhook must be a valid UUID (it becomes a workspace path
  component) and `instance_url` must use the `ws`/`wss` scheme; the new
  `PROVISIONING_ALLOWED_INSTANCE_HOSTS` setting (comma-separated, empty = any
  host) allowlists where device/gateway tokens are sent.

## [4.0.0] - 2026-09-25

### Added
- **CI quality gate**: `pyproject.toml` configures ruff (lint + format,
  rule sets E4/E7/E9/F/I/UP/B), mypy (`src` only, tests exempt), and a
  pytest coverage floor (`fail_under = 48`). The GitHub Actions workflow runs
  ruff, mypy, coverage-gated tests and a Docker image build;
  `requirements-dev.txt` pins the tooling versions. SQLAlchemy models migrated
  to `Mapped[]`/`mapped_column`.
- Initial open source readiness files: `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, and GitHub templates.
- `requirements.lock` for deterministic dependency installs.
- GitHub Actions CI workflow for automated test runs.
- `docs/deployment.md` and `docs/architecture.md` for deployment and architecture guidance.
- `CODEOWNERS` for automated maintainers review.

### Changed
- **Version 4.0.0**: this release bundles the multi-channel router work since
  v3.0.0 — credential encryption, DB-backed state, reliability and the CI
  quality gate (details below).
- **Deployment guide expanded**: real environment-variable list, a table of
  required secrets (`API_TOKEN`, `CREDENTIAL_ENCRYPTION_KEY`,
  `BITRIX_INBOUND_SECRET`, `MM_ACTION_SHARED_SECRET`) with generation
  commands, the one-shot credential-encryption migration step, automatic
  Alembic migrations on container start, and liveness/readiness endpoints.
  Verified end-to-end against a real PostgreSQL 16 (migrations 001→002,
  plaintext→Fernet encryption, idempotent re-run, ORM decrypt round-trip).
- **Teams adapter**: the conversation→serviceUrl map is now LRU-bounded
  (`OrderedDict`, cap 10000) so a long-lived bot cannot grow it unboundedly.
- **WSConnectionManager**: the idle-cleanup loop is started via an explicit
  `await ws_manager.start()` from the app lifespan instead of spawning a task
  in `__init__` via the deprecated `get_event_loop()`.
- **ClawAggregator**: the minimum "real answer" text length is now the
  `CLAW_MIN_VALID_TEXT_LEN` setting (default 5) instead of a hardcoded value.
- **Router state moved to the database**: last-known channels are persisted in a
  new `user_channel` table (proactive delivery now survives restarts and works
  across replicas), and mapping caches are validated against a global
  `mapping_state.version` bumped atomically on every binding — cross-process
  cache invalidation no longer requires the in-process-only
  `POST /api/v1/mappings/reload` (it now just re-reads the version). Adds
  Alembic migration `002`, `MAPPING_CACHE_TTL_SEC` setting; drops the `asyncache`
  dependency.
- Updated README with Docker and local quick start workflows.
- Improved OpenClaw and multi-user isolation documentation.

### Fixed
- **Send idempotency**: a WS disconnect mid-request used to retry `chat.send`
  with a fresh `idempotencyKey`, so OpenClaw could process the same message
  twice. Each logical send now gets one key reused across the retry, and sends
  to the same user are serialized per connection (no more reconnect stampedes
  from concurrent sends).
- **Background tasks**: all fire-and-forget `asyncio.create_task()` call sites
  now go through `src/utils/tasks.py:fire_and_forget()`, which keeps a strong
  reference until completion (unreferenced tasks could be GC'd mid-flight) and
  logs their exceptions.
- **Telegram token leakage in logs**: bot token is now redacted from httpx
  error strings before logging or re-raising (exception messages embed the
  `/bot<token>/method` URL).
- **Readiness probe fail-closed**: `/health/ready` no longer reports
  "mattermost: ok" when the adapter cannot report `is_ws_connected`
  (the `getattr` default was `True`).
- **Cron proactive handler**: a missing `None` guard on the optional
  `on_proactive` callback in the Teams `cron` event path (found by mypy).
- Replaced localized thinking phrases with English equivalents for broader audience.

### Security
- **Teams webhook**: inbound activities now require a valid Bot Framework JWT (signature via Microsoft JWKS, audience, issuer, expiry) and the activity `serviceUrl` must match an HTTPS host whitelist — closes the unauthenticated-webhook and Bearer-token SSRF exfiltration issues.
- **Bitrix24 webhook**: `POST /api/v1/bitrix/event` requires the incoming-webhook secret (`BITRIX_INBOUND_SECRET`); endpoint is disabled when the secret is not configured.
- **Mattermost action proxy**: `POST /api/v1/mm/action` requires a shared secret (`MM_ACTION_SHARED_SECRET`) via header or callback-URL query param; endpoint is disabled when not configured.
- **Credentials at rest**: OpenClaw `private_key_b64`, `device_token` and `gateway_token` are now Fernet-encrypted in PostgreSQL (`CREDENTIAL_ENCRYPTION_KEY`), with backward-compatible plaintext reads and a one-shot `scripts/encrypt_existing_credentials.py` migration.
- Added `PyJWT[crypto]` and (test-only) `aiosqlite` dependencies.
