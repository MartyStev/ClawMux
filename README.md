# ClawMux — OpenClaw WS Router

Open-source control plane that routes chat traffic from Mattermost to per-user OpenClaw instances over persistent WebSocket connections.

ClawMux is built for teams that want strict workspace isolation (`1 user = 1 instance`), proactive agent messages, and a clean API for external triggers.

## What You Can Do

- Route user messages from Mattermost to dedicated OpenClaw instances
- Keep persistent WS sessions with auto-reconnect and idle cleanup
- Trigger async tasks from external systems via `POST /api/v1/trigger`
- Send proactive notifications via `POST /api/v1/notify`
- Proxy files both ways: Mattermost attachments ↔ OpenClaw workspace
- Export health and metrics endpoints for observability (`/health`, `/metrics`)

## Architecture

```text
Mattermost WS/HTTP
      │
      ▼
  ClawMux Router
      ├── MappingStorage (PostgreSQL)
      ├── WSConnectionManager (persistent OpenClaw WS per user)
      ├── Control-Plane API (/api/v1/trigger, /api/v1/notify)
      └── FileManager (attachments/media)
      │
      ▼
OpenClaw Gateway instances (isolated per user)
```

### Identity model (future-ready)

ClawMux already uses a provider-aware user model:

- `app_user` — canonical internal user
- `user_identity` — channel identity (`provider`, `provider_user_id`)
- `user_instance` — active mapping to OpenClaw instance

`provider` is part of API contracts today, while runtime support is currently enabled for `mattermost`.

## Quick Start

### 1. Configure environment

```bash
git clone <your-fork-or-repo-url>
cd ClawMux
cp .env.example .env
```

Fill in `.env` values (Mattermost URL/token, API token, etc.).

### 2. Start services

```bash
docker compose up -d --build
```

This compose stack includes:

- `postgres` (local DB for ClawMux)
- `ws-router` (FastAPI app + Alembic migrations on startup)

### 3. Verify

```bash
curl http://localhost:8060/health
```

## User Onboarding

When an OpenClaw `instance` row already exists, bind a user with one command:

```bash
scripts/onboard_user.sh \
  --app-user-id mm:u_abc123 \
  --external-user-id ext-1001 \
  --provider mattermost \
  --provider-user-id u_abc123 \
  --instance-uuid 30f2aeff-1111-2222-3333-123456789abc
```

What this does:

- upsert into `app_user`
- upsert into `user_identity`
- enforce reassignment in `user_instance` (1:1 mapping)

## Control-Plane API

### `POST /api/v1/trigger`

Asynchronously dispatch a task to the mapped user instance.

```bash
curl -X POST http://localhost:8060/api/v1/trigger \
  -H "X-Api-Token: change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id": "user-ext-123",
    "provider": "mattermost",
    "text": "Generate weekly summary"
  }'
```

### `POST /api/v1/notify`

Send proactive notification to user channel.

```bash
curl -X POST http://localhost:8060/api/v1/notify \
  -H "X-Api-Token: change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id": "user-ext-123",
    "provider": "mattermost",
    "text": "Reminder: standup in 10 minutes"
  }'
```

Detailed API docs: [docs/control-plane-api.md](docs/control-plane-api.md)

## Security

- No secrets in repository templates (`.env.example` is sanitized)
- API access protected by `X-Api-Token`
- Channel-level identity mapping separated from internal user model
- Per-user OpenClaw instance isolation

## Configuration

Main variables in `.env.example`:

- `DATABASE_URL`
- `MATTERMOST_URL`
- `MATTERMOST_TOKEN`
- `API_TOKEN`
- `MM_ACTION_PROXY_URL`
- `WORKSPACE_BASE_PATH`
- `DIFY_BASE_URL`, `DIFY_API_KEY`

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m src.main
```

## Project Structure

```text
src/
  api/          # FastAPI endpoints
  core/         # config, db, models
  services/     # Mattermost/OpenClaw clients, mapping, files
  utils/        # health, metrics, helpers
alembic/        # migrations
scripts/        # operational scripts
docs/           # design and API docs
```

## Roadmap

- Add channel adapters beyond Mattermost (Slack/Telegram/etc.)
- Add provider-specific proactive delivery strategies
- Add automatic user registration on first inbound message to router
- Add automatic OpenClaw instance provisioning for newly registered users
- Add integration tests for mapping and trigger/notify flows
- Add production deployment guide (HA, backups, secret management)

## Open Source Notes

This repository is prepared for public use:

- neutral naming (`external_user_id`, provider-aware identities)
- no hardcoded private infra in defaults
- local PostgreSQL in compose for reproducible startup
