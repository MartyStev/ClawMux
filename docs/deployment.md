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

- `MATTERMOST_URL`
- `MATTERMOST_TOKEN`
- `DATABASE_URL`
- `OPENCLAW_URL`
- `DIFY_API_KEY` (optional fallback)
- `API_TOKEN`

### Health check

Verify the service is running:

```bash
curl http://localhost:8060/health
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
