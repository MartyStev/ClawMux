# ClawMux — AI Router for OpenClaw

ClawMux is a lightweight multi-user control plane that routes Mattermost chat traffic to per-user OpenClaw instances over persistent WebSocket connections.

It is designed as an isolation-first solution for organizations that need strict multi-user separation across OpenClaw workspaces. It solves the operational gap between chat systems and isolated OpenClaw workspaces by providing:

- dedicated OpenClaw routing per user
- proactive outbound notifications to OpenClaw users
- external trigger API for OpenClaw workloads
- file proxying between Mattermost and OpenClaw workspaces
- health and metrics endpoints for observability of OpenClaw routing

## What ClawMux Does

- **Route messages from Mattermost** to a mapped OpenClaw instance
- **Keep persistent WebSocket sessions** with auto-reconnect and idle cleanup
- **Deliver proactive notifications** back into Mattermost
- **Proxy attachments and media** between Mattermost and OpenClaw workspaces
- **Expose a control-plane API** for external task triggers
- **Fallback to Dify** when user mapping is missing and DIFY API key is configured

## What Is an Instance?

In ClawMux, an instance is a user-specific OpenClaw workspace reachable via its gateway URL.

Each instance is treated as an isolated AI workspace:

- one OpenClaw instance per user mapping
- per-channel identity via `provider` + `provider_user_id`
- persistent WS connectivity to send/receive messages
- file context injected from Mattermost attachments

This repository does not provision containers itself; it routes traffic to already provisioned OpenClaw instances and keeps the session alive.

## OpenClaw Integration

ClawMux is built specifically for OpenClaw integration and supports OpenClaw gateway routing, OpenClaw session management, and OpenClaw attachment synchronization.

- routes Mattermost messages into OpenClaw agent workspaces
- forwards OpenClaw proactive messages back to Mattermost
- downloads and uploads files on behalf of OpenClaw instances
- maintains user mappings for OpenClaw identities in PostgreSQL

This makes ClawMux an ideal companion for OpenClaw deployments where each user has a dedicated OpenClaw workspace.

## Architecture

```text
Mattermost WS/HTTP
      │
      ▼
  ClawMux Router
      ├─ MappingStorage (PostgreSQL)
      ├─ WSConnectionManager (persistent OpenClaw WS)
      ├─ Router core (message and proactive delivery)
      ├─ Control-Plane API (/api/v1/trigger, /api/v1/notify)
      └─ FileManager (attachments/media sync)
      │
      ▼
OpenClaw instances (one per user mapping)
```

The router maintains a provider-aware identity model and a live mapping from external users to OpenClaw instance URLs.

## Access Control

ClawMux is built as a multi-user isolation solution with strong boundaries between users and their OpenClaw workspaces.

- `external_user_id` + `provider` identity keys
- `user_instance` mappings stored in PostgreSQL
- API authentication via `X-Api-Token`
- scoped delivery only to mapped instances
- per-user instance routing to prevent cross-user access
- all Mattermost traffic is routed through a single bot channel

Today the runtime channel adapter is built for `mattermost`, but the patterns are provider-agnostic.

## Control-Plane API

All control-plane requests require an `X-Api-Token` header.

### `POST /api/v1/trigger`

Send an asynchronous task to the mapped user's OpenClaw instance.

```bash
curl -X POST http://localhost:8060/api/v1/trigger \
  -H "X-Api-Token: ${API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id": "user-ext-123",
    "provider": "mattermost",
    "text": "Сделай краткий отчёт по продажам"
  }'
```

### `POST /api/v1/notify`

Send a proactive notification into Mattermost for the mapped user.

```bash
curl -X POST http://localhost:8060/api/v1/notify \
  -H "X-Api-Token: ${API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id": "user-ext-123",
    "provider": "mattermost",
    "text": "Напоминание: встреча через 10 минут"
  }'
```

See [docs/control-plane-api.md](docs/control-plane-api.md) for full request and response details.

## Security

- `X-Api-Token` protects control-plane endpoints
- channel identity is separated from internal user mapping
- instance URLs stay behind the router
- no hardcoded secrets in repository templates
- optional Dify fallback only activates when configured

## Quick Start

### 1. Clone and configure

```bash
git clone <your-repo-url>
cd ClawMux
cp .env.example .env
```

Edit `.env` with your Mattermost URL, tokens, database URL, and optional Dify settings.

### 2. Run with Docker Compose

```bash
docker compose up -d --build
```

### 3. Check health

```bash
curl http://localhost:8060/health
```

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
  api/          # FastAPI endpoints for trigger, notify, Mattermost actions
  core/         # config, database, and ORM models
  services/     # Mattermost adapter, OpenClaw WS manager, mapping, file proxy
  utils/        # health, metrics, helpers
alembic/        # database migrations
scripts/        # onboarding and maintenance scripts
docs/           # API and architecture docs
```

This repository is optimized for OpenClaw routing use cases and includes a service layer designed to manage OpenClaw WebSocket sessions, OpenClaw user mappings, and OpenClaw-aware message delivery.

## Onboarding Users

Use `scripts/onboard_user.sh` to bind an external user to an existing OpenClaw instance:

```bash
scripts/onboard_user.sh \
  --app-user-id mm:u_abc123 \
  --external-user-id user-ext-123 \
  --provider mattermost \
  --provider-user-id u_abc123 \
  --instance-uuid 30f2aeff-1111-2222-3333-123456789abc
```

This creates or updates:

- `app_user`
- `user_identity`
- `user_instance`

## Documentation

- [Control Plane API](docs/control-plane-api.md)
- [docs/control-plane-api.md](docs/control-plane-api.md)

## Roadmap

- support additional channel adapters beyond Mattermost
- add automated instance provisioning
- add UI/dashboard for mapping and health
- improve production deployment docs and secret management
- add integration tests for async routing and file proxy

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=martystev/ClawMux&type=date&legend=top-left)](https://www.star-history.com/#martystev/ClawMux&type=date&legend=top-left)
