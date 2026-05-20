# Real OpenClaw Integration Guide

## Prerequisites

### 1. LM Studio Setup
Make sure LM Studio is running on your machine:
```bash
# LM Studio should be running on port 1234
# Download model: qwen/qwen3.5-9b
# Make sure it's loaded and responding to API calls
curl http://127.0.0.1:1234/v1/models
```

### 2. OpenClaw Docker Image
OpenClaw can be obtained directly from GitHub Container Registry or built from local source:
```bash
# Option 1: Pull from registry
docker pull ghcr.io/openclaw/openclaw:latest

# Option 2: Build from local source (if you have it)
# Set OPENCLAW_SRC to the directory containing the OpenClaw Dockerfile
OPENCLAW_SRC=/path/to/openclaw-source docker build -t ghcr.io/openclaw/openclaw:latest "$OPENCLAW_SRC"
```

If you use a custom image, set this variable:
```bash
export OPENCLAW_IMAGE=your-registry/openclaw:tag
```

## Deployment

### Step 1: Start LM Studio on Host
```bash
# LM Studio must be running and reachable at http://127.0.0.1:1234
# The qwen/qwen3.5-9b model must be loaded
```

### Step 2: Start Docker Services
```bash
cd /Users/martystev/VS/personal/ClawMux
docker compose down --remove-orphans
docker compose -f docker-compose.prod.yml up -d --build
```

`docker-compose.prod.yml` now mounts a persistent volume at `/home/node/.openclaw`, so OpenClaw identity/runtime config is preserved across restarts and recreates.

### Step 3: Configure the Database
For real OpenClaw, you must register full instance credentials in `ws_router.instance` (not only `instance_url`):

```bash
# Example UUID already used in this stack:
OPENCLAW_INSTANCE_UUID="2f99d082-71fb-4bf7-a4c5-cfeea78976c6"

# Gateway token from /home/node/.openclaw/openclaw.json inside the openclaw container
GATEWAY_TOKEN="test-gateway-token-001"

scripts/register_real_openclaw_instance.sh \
  --instance-uuid "$OPENCLAW_INSTANCE_UUID" \
  --gateway-token "$GATEWAY_TOKEN" \
  --instance-url ws://openclaw:18789/ws

# Trigger any request through clawmux, then approve pairing:
docker compose exec -T openclaw openclaw devices approve --latest --json
```

### Step 4: Apply OpenClaw Runtime Defaults
```bash
./scripts/configure_openclaw_runtime.sh
```

This pins runtime to `pi` and provider API to `openai-responses`, which avoids:
- `Requested agent harness "codex" is not registered`
- SSRF blocks when using `openai-completions` against internal Docker DNS hostnames

The default model in this script is `lmstudio/qwen3.5-9b`.
The script also sets gateway token/auth defaults; after it runs, make sure the DB mapping uses the same token via `scripts/register_real_openclaw_instance.sh`.

## Verification

### 1. Check Services
```bash
./scripts/test_integration.sh
```

### 2. Check OpenClaw Logs
```bash
docker compose logs -f openclaw
```

### 3. Test Message Routing
```bash
./scripts/test_routing.sh
```

### 4. Monitor Router
```bash
docker compose logs -f clawmux
```

## Troubleshooting

### LM Studio Connection Issues
If the nginx proxy can't reach LM Studio:
```bash
# Verify LM Studio is running on the host
curl http://127.0.0.1:1234/v1/models

# Test from the nginx container
docker compose exec lmstudio-proxy curl http://host.docker.internal:1234/v1/models
```

If you see `model_load_failed` in LM Studio logs, reduce model size/quantization or increase available memory in LM Studio runtime settings.

### OpenClaw Connection Issues
If clawmux can't connect to OpenClaw:
```bash
# Check OpenClaw logs
docker compose -f docker-compose.prod.yml logs openclaw

# Verify OpenClaw is listening
docker compose -f docker-compose.prod.yml exec clawmux python - <<'PY'
import socket
s = socket.socket()
s.settimeout(2)
s.connect(("openclaw", 18789))
print("openclaw:18789 is reachable from clawmux")
PY

# Test WS connection
docker compose -f docker-compose.prod.yml logs --tail=100 clawmux
```

If logs contain `Requested agent harness "codex" is not registered`, rerun:
```bash
./scripts/configure_openclaw_runtime.sh
```

### Database Issues
```bash
# Check OpenClaw database
docker compose exec postgres psql -U router -d openclaw -c "SELECT * FROM instances;"
```

## Network Communication Flow

```
User (Mattermost) 
    ↓
ClawMux (clawmux:8060)
    ↓
OpenClaw Instance (openclaw:18789/ws in Docker network)
    ↓
LM Studio Proxy (nginx:1234)
    ↓
LM Studio (host:127.0.0.1:1234)
```

## Configuration Variables

Edit `.env` or docker-compose to customize:

```bash
# LM Model
LLM_MODEL=qwen/qwen3.5-9b

# OpenClaw Instance Name
INSTANCE_NAME=ClawMux-Main

# OpenClaw Device ID
DEVICE_ID=clawmux-device-001

# Gateway URL (for OpenClaw to find clawmux)
GATEWAY_URL=http://clawmux:8060
```
