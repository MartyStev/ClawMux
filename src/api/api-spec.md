# OpenClaw WS Router — API Spec

Version: 1.2

## Authentication

All `control-plane` endpoints require:

```text
X-Api-Token: <API_TOKEN>
```

## POST /api/v1/trigger

Fire-and-forget task dispatch to a specific user.

Request:

```json
{
  "external_user_id": "user-ext-123",
  "text": "Generate weekly sales summary",
  "session_key": "agent:main:main"
}
```

Success response:

```json
{
  "status": "sent",
  "request_id": "f4a1b2c3-d4e5-6789-abcd-ef0123456789"
}
```

## POST /api/v1/notify

System notification to a specific user.

Request:

```json
{
  "external_user_id": "user-ext-123",
  "text": "Reminder: standup in 10 minutes"
}
```

Success response:

```json
{
  "status": "sent"
}
```

## Error codes

- `401` invalid or missing API token
- `404` no instance mapping for provided user id
- `422` malformed JSON or missing required fields
