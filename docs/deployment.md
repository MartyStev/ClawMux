# Deployment Guide

This guide covers the main deployment options for ClawMux.

## Docker Compose

The easiest way to run ClawMux is with Docker Compose.

```bash
docker compose up -d --build
```

This will build the service container and start it with the configuration from `.env`.

### Environment variables

Copy `.env.example` to `.env` and update the values:

- `DATABASE_URL`
- `MATTERMOST_URL`, `MATTERMOST_TOKEN`, `MATTERMOST_BOT_USERNAME`
- `API_TOKEN` — control-plane secret (`X-Api-Token`)
- `OPENCLAW_CONFIGS_PATH` — host path mounted at `/configs` for workspaces

Optional channels (set `ENABLE_*=true` plus their tokens): Telegram, Slack,
VK Teams, Bitrix24, Microsoft Teams, Dify fallback. See `.env.example` for
the full list.

### Required secrets for exposed endpoints

| Variable | Purpose | If empty |
|---|---|---|
| `API_TOKEN` | Protects `/api/v1/trigger` and `/api/v1/notify` | endpoints reject all requests |
| `CREDENTIAL_ENCRYPTION_KEY` | Fernet-encrypts OpenClaw private keys/tokens at rest in PostgreSQL | credentials stored as plaintext (dev only; startup logs a warning) |
| `BITRIX_INBOUND_SECRET` | Verifies inbound events to `/api/v1/bitrix/event` | endpoint disabled |
| `MM_ACTION_SHARED_SECRET` | Verifies `/api/v1/mm/action` callbacks from Mattermost | endpoint disabled |

Generate strong values:

```bash
# Fernet key for credential encryption
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Any of the remaining secrets
openssl rand -hex 32
```

If the database already contains instances (plaintext credentials), run the
one-shot encryption script once after setting `CREDENTIAL_ENCRYPTION_KEY`
(`--dry-run` first to preview):

```bash
python scripts/encrypt_existing_credentials.py --dry-run
python scripts/encrypt_existing_credentials.py
```

Already-encrypted rows are skipped, so re-running is safe.

### Database migrations

Migrations run automatically on container start (`docker/entrypoint.sh`
executes `alembic upgrade head` before uvicorn). For local runs execute them
manually before starting the app. Current schema is at revision `002`
(`user_channel` + `mapping_state`).

### Health check

Verify the service is running:

```bash
curl http://localhost:8060/health          # liveness
curl http://localhost:8060/health/ready    # readiness (DB + channel adapters)
```

## Local Python deployment

For local development without Docker:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
alembic upgrade head
python -m src.main
```

This runs the service directly in Python using the same configuration from `.env`.

## Production considerations

- Run behind a reverse proxy or load balancer.
- Use a managed PostgreSQL instance for reliable storage.
- Store secrets in a secure vault or environment management system.
- Configure logging and metrics collection to monitor OpenClaw WS activity.
- Use GitHub Actions or another CI/CD pipeline to validate changes before deploy.
