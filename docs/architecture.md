# Architecture Guide

ClawMux is designed as a lightweight multi-user router for OpenClaw workspaces.

## High-level architecture

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

## Components

### Routing core

- Accepts incoming Mattermost events and external control-plane requests.
- Resolves user identity via `provider` + `provider_user_id`.
- Uses user mapping to route messages to the correct OpenClaw workspace.

### MappingStorage

- Stores `app_user`, `user_identity`, and `user_instance` entries in PostgreSQL.
- Ensures each external user is mapped to only one OpenClaw instance.
- Supports cache invalidation and TTL-aware mapping lookup.

### WSConnectionManager

- Maintains persistent WebSocket connections to OpenClaw instances.
- Reconnects automatically on failure and drains idle connections.
- Streams inbound OpenClaw events back into Mattermost via the router.

### FileManager

- Proxies attachments and media between Mattermost and OpenClaw instances.
- Downloads files from Mattermost and uploads them to OpenClaw when needed.

### Control-plane API

- `POST /api/v1/trigger` for async task triggers.
- `POST /api/v1/notify` for proactive Mattermost notifications.
- Protected by `X-Api-Token` authentication.

## Isolation model

- One OpenClaw instance per user mapping.
- No shared chat state between users.
- Single bot channel in Mattermost for all inbound messages.
- Instance URLs and internal routing remain hidden behind the ClawMux service.

## Security and observability

- Keep API tokens and database credentials out of source control.
- Use `requirements.lock` for deterministic dependency installs.
- Monitor health and metrics for WebSocket connectivity and active mappings.
