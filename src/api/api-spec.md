# ClawMux — API Spec

Version: 1.3

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
  "provider": "mattermost",
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
  "provider": "mattermost",
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
- `400` provider is not enabled
- `422` malformed JSON or missing required fields


Only `mattermost` is enabled in the current runtime; `provider` is added for forward-compatible multi-channel API contracts.
