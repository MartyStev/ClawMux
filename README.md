# ClawMux — Omni-Channel AI Router for OpenClaw

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/martystev/ClawMux/actions/workflows/ci.yml/badge.svg)](https://github.com/martystev/ClawMux/actions/workflows/ci.yml)

ClawMux is a lightweight multi-user control plane and omni-channel AI router that dispatches chat traffic from **Mattermost**, **Telegram**, **Bitrix24**, **Slack**, **VK Teams (Myteam)**, and **Microsoft Teams** to per-user OpenClaw instances over persistent WebSocket connections.

This repository includes open-source readiness files such as `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and GitHub issue/PR templates.

It is designed as an isolation-first solution for organizations that need strict multi-user separation across OpenClaw workspaces. It solves the operational gap between corporate chat systems and isolated OpenClaw workspaces by providing:

- omni-channel routing (**Mattermost**, **Telegram**, **Bitrix24**, **Slack**, **VK Teams**, **MS Teams**)
- dedicated OpenClaw routing per user
- proactive outbound notifications to OpenClaw users
- external trigger API for OpenClaw workloads
- real-time response streaming and typing indicators
- file proxying between chat platforms and OpenClaw workspaces
- health and readiness probes (`/health/live`, `/health/ready`) and Prometheus metrics

## Supported Chat Platforms

| Provider | Transport / Protocol | Mode | Real-time Streaming | Threads / Quotes |
|---|---|---|---|---|
| **Mattermost** | WebSocket + REST | Active Listener | ✅ (Debounced) | ✅ `root_id` |
| **Telegram** | Long Polling / REST | Active Listener | ✅ `editMessageText` | ✅ `message_thread_id` |
| **Bitrix24** | Webhook + REST | Webhook Endpoint | ✅ `imbot.message.update` | ✅ |
| **Slack** | Socket Mode (WS) + REST | Active Listener | ✅ `chat.update` | ✅ `thread_ts` |
| **VK Teams** | Long Polling + REST | Active Listener | ✅ `messages/editText` | ✅ `replyMsgId` |
| **MS Teams** | Webhook + OAuth2 REST | Webhook Endpoint | ✅ Activity Update | ✅ `replyToId` |

## What ClawMux Does

- **Route messages from corporate chat platforms** to mapped OpenClaw instances
- **Keep persistent WebSocket sessions** with auto-reconnect and idle cleanup
- **Deliver proactive notifications** back into the user's active channel
- **Proxy attachments and media** between corporate chats and OpenClaw workspaces
- **Expose a control-plane API** for external task triggers
- **Fallback to Dify** when user mapping is missing and DIFY API key is configured

## Architecture

```text
Corporate Messengers (Mattermost, Telegram, Bitrix24, Slack, VK Teams, MS Teams)
      │
      ▼
  ClawMux Router (Multi-Channel Dispatcher)
      ├─ ProviderRegistry (Chat Adapters Layer)
      ├─ MappingStorage (PostgreSQL)
      ├─ WSConnectionManager (persistent OpenClaw WS)
      ├─ Control-Plane API (/api/v1/trigger, /api/v1/notify, /api/v1/mappings/reload)
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
    "text": "Generate a short sales report"
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
    "text": "Reminder: meeting in 10 minutes"
  }'
```

See [docs/control-plane-api.md](docs/control-plane-api.md) for full request and response details.

## Security

- `X-Api-Token` protects control-plane endpoints
- channel identity is separated from internal user mapping
- instance URLs stay behind the router
- no hardcoded secrets in repository templates
- optional Dify fallback only activates when configured

## Open Source Readiness

- `LICENSE` for open-source distribution
- `CONTRIBUTING.md` for contribution guidance
- `SECURITY.md` for responsible disclosure
- `CODE_OF_CONDUCT.md` for community expectations
- GitHub issue and pull request templates for contributors
- GitHub Actions CI for automated testing on push and PRs

## Quick Start

### 1. Clone and configure

```bash
git clone <your-repo-url>
cd ClawMux
cp .env.example .env
```

Edit `.env` with your Mattermost URL, tokens, database URL, and optional Dify settings.

Use `requirements.lock` for deterministic installs when you want a reproducible environment.

### 2. Docker quick start (with local testing stack)

For easy testing, the compose includes PostgreSQL, Mattermost, and a mock OpenClaw server:

```bash
docker compose up -d --build
```

This starts:
- PostgreSQL on `:5432`
- Mattermost on `http://localhost:8065` (admin: `admin@example.com` / `admin123`)
- Mock OpenClaw WS on `ws://localhost:18789`
- ClawMux on `http://localhost:8060`

Wait for the service to start and verify health:

```bash
curl http://localhost:8060/health
```

Run the integration test:

```bash
./scripts/test_integration.sh
```

#### Setting up Mattermost bot for testing

After starting the services:

1. Open Mattermost at `http://localhost:8065`
2. Login with admin@example.com / admin123
3. Create a team (or use default)
4. Create a channel for testing
5. The bot token is already configured in `.env`

The bot will automatically connect when the router starts.

### 3. Manual setup (production)

For production deployment, see [docs/deployment.md](docs/deployment.md).

### 3. Local Python quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
alembic upgrade head
python -m src.main
```

Then check health:

```bash
curl http://localhost:8060/health
```

## Deployment docs

For more deployment options and production guidance, see [docs/deployment.md](docs/deployment.md).

## Architecture docs

For architecture details and component flow, see [docs/architecture.md](docs/architecture.md).

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
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

- [Control Plane API](docs/control-plane-api.md)- [Deployment guide](docs/deployment.md)
- [Architecture guide](docs/architecture.md)- `requirements.lock` for deterministic installs
- `LICENSE` for open-source distribution
- `CONTRIBUTING.md` for contribution guidelines
- `SECURITY.md` for responsible disclosure

## Roadmap

- support additional channel adapters beyond Mattermost
- add automated instance provisioning
- add UI/dashboard for mapping and health
- improve production deployment docs and secret management
- add integration tests for async routing and file proxy

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=martystev/ClawMux&type=date&legend=top-left)](https://www.star-history.com/#martystev/ClawMux&type=date&legend=top-left)
