#!/bin/bash
# Simple integration test for ClawMux

echo "=== ClawMux Integration Test ==="

# Check services
echo "1. Checking WS Router health..."
curl -s http://localhost:8060/health | jq -r '.status' || echo "FAILED"

echo "2. Checking Mattermost..."
curl -s http://localhost:8065/api/v4/system/ping | jq -r '.status' || echo "FAILED"

echo "3. Checking PostgreSQL..."
docker compose exec -T postgres pg_isready -U router -d ws_router >/dev/null && echo "OK" || echo "FAILED"

echo "4. Checking OpenClaw mock..."
# Simple WS test - just check if port is open
nc -z localhost 18789 && echo "OK" || echo "FAILED"

echo "=== Test Complete ==="
echo "For manual testing:"
echo "- Mattermost: http://localhost:8065 (admin@example.com / admin123)"
echo "- WS Router API: http://localhost:8060"
echo "- OpenClaw mock WS: ws://localhost:18789"