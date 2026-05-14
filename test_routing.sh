#!/bin/bash
# Test message routing through ClawMux

# Your Mattermost user ID
EXTERNAL_USER_ID="b7tau3ictbrfpfbj6zo314r6nr"
API_TOKEN="${API_TOKEN:-change-me-to-a-strong-secret}"

echo "=== Testing ClawMux Message Routing ==="
echo "User ID: $EXTERNAL_USER_ID"
echo ""

# Test the trigger endpoint
echo "1. Testing trigger endpoint (POST /api/v1/trigger)..."
curl -X POST http://localhost:8060/api/v1/trigger \
  -H "Content-Type: application/json" \
  -H "X-Api-Token: $API_TOKEN" \
  -d '{
    "external_user_id": "'$EXTERNAL_USER_ID'",
    "provider": "mattermost",
    "text": "Hello OpenClaw! This is a test message from ClawMux.",
    "session_key": "agent:main:main"
  }' | jq .

echo ""
echo "2. Testing notify endpoint (POST /api/v1/notify)..."
curl -X POST http://localhost:8060/api/v1/notify \
  -H "Content-Type: application/json" \
  -H "X-Api-Token: $API_TOKEN" \
  -d '{
    "external_user_id": "'$EXTERNAL_USER_ID'",
    "provider": "mattermost",
    "text": "System notification: Your OpenClaw agent is online!"
  }' | jq .

echo ""
echo "=== Test Complete ==="
echo ""
echo "Next steps:"
echo "1. Open Mattermost: http://localhost:8065"
echo "2. Check your direct messages for bot responses"
echo "3. Monitor logs: docker compose logs -f ws-router"