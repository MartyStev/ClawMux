# Control-Plane API

## Purpose

The Control-Plane API allows external systems to send tasks and notifications to OpenClaw users through the router.

Base URL:

```text
http://<router-host>:8060/api/v1
```

Authentication for all endpoints:

```text
X-Api-Token: <API_TOKEN>
```

The `API_TOKEN` is configured in `.env`.

## 1) POST /trigger

Asynchronously sends a task to a user. A `200` response means the router accepted the task and queued it for processing.

### Request

```json
{
  "external_user_id": "user-ext-123",
  "provider": "mattermost",
  "text": "Create a short sales report",
  "session_key": "agent:main:main"
}
```

Fields:

- `external_user_id` (string, required): external identifier of the user in your system
- `provider` (string, optional): channel provider, defaults to `mattermost`
- `text` (string, required): task text
- `session_key` (string, optional): OpenClaw session key

### Response 200

```json
{
  "status": "sent",
  "request_id": "f4a1b2c3-d4e5-6789-abcd-ef0123456789"
}
```

## 2) POST /notify

Sends a system notification directly to a user.

### Request

```json
{
  "external_user_id": "user-ext-123",
  "provider": "mattermost",
  "text": "Reminder: daily stand-up in 10 minutes"
}
```

### Response 200

```json
{
  "status": "sent"
}
```

## Error codes

- `401 Unauthorized`: invalid or missing `X-Api-Token`
- `404 Not Found`: user mapping not found
- `400 Bad Request`: provider is not enabled
- `422 Unprocessable Entity`: invalid request body

## Example `curl`

```bash
curl -X POST http://localhost:8060/api/v1/trigger \
  -H "X-Api-Token: change-me-to-a-strong-secret" \
  -H "Content-Type: application/json" \
  -d '{"external_user_id":"user-ext-123","provider":"mattermost","text":"Create a report"}'
```


Important: the multi-channel structure already exists in the database, but only `mattermost` is enabled at runtime.
