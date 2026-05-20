#!/bin/bash
# Test message routing through ClawMux

API_TOKEN="${API_TOKEN:-change-me-to-a-strong-secret}"

# Pick mapped external_user_id from DB unless explicitly provided.
EXTERNAL_USER_ID="${EXTERNAL_USER_ID:-}"
if [ -z "$EXTERNAL_USER_ID" ]; then
  EXTERNAL_USER_ID="$(
    docker compose exec -T postgres psql -U router -d ws_router -Atc \
      "SELECT external_user_id FROM app_user WHERE external_user_id IS NOT NULL ORDER BY created_at DESC LIMIT 1;"
  )"
fi

if [ -z "$EXTERNAL_USER_ID" ]; then
  echo "No mapped external_user_id found in app_user."
  echo "Run onboarding first, for example:"
  echo "  scripts/onboard_user.sh --app-user-id ... --external-user-id ... --provider-user-id ... --instance-uuid ..."
  exit 1
fi

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
echo "3. Monitor logs: docker compose logs -f clawmux"
