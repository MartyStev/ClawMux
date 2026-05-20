# ClawMux Quick Start — Setup Complete ✅

## Summary

Your ClawMux setup is now fully operational! Here's what was configured:

### Services Running
- **Mattermost**: http://localhost:8065 (admin@example.com / admin123)
- **PostgreSQL**: localhost:5432
- **OpenClaw Mock**: ws://localhost:18789
- **ClawMux API**: http://localhost:8060

### User Configuration
- **External User ID (Mattermost)**: `b7tau3ictbrfpfbj6zo314r6nr`
- **Bot Token**: `yyn6w1dxst8tjrskr3xeom8uuc`
- **Status**: ✅ Connected and routing messages

## Testing

### Health Check
```bash
curl http://localhost:8060/health | jq
```

### Full Integration Test
```bash
./scripts/test_integration.sh
```

### Message Routing Test
```bash
./scripts/test_routing.sh
```

## API Endpoints

### POST /api/v1/trigger
Send a message from Mattermost to OpenClaw (fire-and-forget).

```bash
curl -X POST http://localhost:8060/api/v1/trigger \
  -H "Content-Type: application/json" \
  -H "X-Api-Token: change-me-to-a-strong-secret" \
  -d '{
    "external_user_id": "b7tau3ictbrfpfbj6zo314r6nr",
    "provider": "mattermost",
    "text": "Your message here",
    "session_key": "agent:main:main"
  }'
```

### POST /api/v1/notify
Send a system notification to the user.

```bash
curl -X POST http://localhost:8060/api/v1/notify \
  -H "Content-Type: application/json" \
  -H "X-Api-Token: change-me-to-a-strong-secret" \
  -d '{
    "external_user_id": "b7tau3ictbrfpfbj6zo314r6nr",
    "provider": "mattermost",
    "text": "System notification"
  }'
```

## Next Steps

### 1. Connect with Real OpenClaw Instance
Update the database with your actual OpenClaw instance:

```bash
docker compose exec -T postgres psql -U router -d ws_router << 'EOF'
UPDATE instance 
SET instance_url = 'ws://your-openclaw-host:port'
WHERE instance_uuid = '2f99d082-71fb-4bf7-a4c5-cfeea78976c6';
EOF

docker compose restart clawmux
```

### 2. Set API Token (Required for Production)
```bash
# Generate strong token
openssl rand -hex 32

# Update .env
echo "API_TOKEN=<your-strong-token>" >> .env

docker compose restart clawmux
```

### 3. Monitor Logs
```bash
docker compose logs -f clawmux
```

### 4. Add More Users
```sql
-- Repeat for each user with their actual Mattermost user ID
INSERT INTO app_user (id, external_user_id, role, created_at)
VALUES ('user-' || substring(md5(random()::text), 1, 12), '<mattermost_user_id>', 'user', NOW());
```

## Troubleshooting

### Connection Errors
Check if OpenClaw instance is accessible:
```bash
nc -zv your-openclaw-host port
```

### Database Issues
Check PostgreSQL connection:
```bash
docker compose exec postgres pg_isready -U router -d ws_router
```

### Message Not Appearing
Check Mattermost bot token is correct and bot has channel permissions

## Files Created

- `docker-compose.yml` - Full stack with services
- `docker/Dockerfile.mock` - Mock OpenClaw server
- `scripts/mock_openclaw.py` - Mock implementation
- `scripts/test_integration.sh` - Full integration test
- `scripts/test_routing.sh` - API routing test
- `.env` - Configuration with your bot token
- `.env.example` - Template for new setups