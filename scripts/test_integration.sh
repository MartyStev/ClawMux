#!/bin/bash
# Simple integration test for ClawMux

echo "=== ClawMux Integration Test ==="

# Check services
echo "1. Checking ClawMux health..."
curl -s http://localhost:8060/health | jq -r '.status' || echo "FAILED"

echo "2. Checking Mattermost..."
curl -s http://localhost:8065/api/v4/system/ping | jq -r '.status' || echo "FAILED"

echo "3. Checking PostgreSQL..."
docker compose exec -T postgres pg_isready -U router -d ws_router >/dev/null && echo "OK" || echo "FAILED"

echo "4. Checking OpenClaw endpoint from clawmux..."
docker compose exec -T clawmux python - <<'PY'
import socket
for host, port in [("openclaw-mock", 18789), ("openclaw", 18789)]:
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((host, port))
        print(f"OK ({host}:{port})")
        raise SystemExit(0)
    except Exception:
        pass
    finally:
        s.close()
print("FAILED (no OpenClaw endpoint reachable on :18789)")
raise SystemExit(1)
PY

echo "=== Test Complete ==="
echo "For manual testing:"
echo "- Mattermost: http://localhost:8065 (admin@example.com / admin123)"
echo "- ClawMux API: http://localhost:8060"
echo "- OpenClaw mock WS: ws://localhost:18789 (default compose)"
echo "- OpenClaw real WS (docker network): ws://openclaw:18789/ws"
