# Real OpenClaw Integration Guide

## Prerequisites

### 1. LM Studio Setup
Убедитесь, что LM Studio запущен на вашей машине:
```bash
# LM Studio should be running on port 1234
# Download model: qwen/qwen3.5-9b
# Make sure it's loaded and responding to API calls
curl http://127.0.0.1:1234/v1/models
```

### 2. OpenClaw Docker Image
OpenClaw можно получить напрямую из GitHub Container Registry или сборкой из локального источника:
```bash
# Option 1: Pull from registry
docker pull ghcr.io/openclaw/openclaw:latest

# Option 2: Build from local source (if you have it)
# Set OPENCLAW_SRC to the directory containing the OpenClaw Dockerfile
OPENCLAW_SRC=/path/to/openclaw-source docker build -t ghcr.io/openclaw/openclaw:latest "$OPENCLAW_SRC"
```

Если вы используете кастомный образ, установите переменную:
```bash
export OPENCLAW_IMAGE=your-registry/openclaw:tag
```
## Deployment

### Step 1: Start LM Studio on Host
```bash
# LM Studio должен быть запущен и доступен на http://127.0.0.1:1234
# Модель qwen/qwen3.5-9b должна быть загружена
```

### Step 2: Start Docker Services
```bash
cd /Users/martystev/VS/personal/ClawMux
docker compose down --remove-orphans
docker compose -f docker-compose.prod.yml up -d --build
```
`docker-compose.prod.yml` now mounts a persistent volume at `/home/node/.openclaw`,
so OpenClaw identity/runtime config is preserved across `restart`/`recreate`.

### Step 3: Configure Database
For real OpenClaw you must register full instance credentials in `ws_router.instance`
(not only `instance_url`):

```bash
# Example UUID already used in this stack:
OPENCLAW_INSTANCE_UUID="2f99d082-71fb-4bf7-a4c5-cfeea78976c6"

# Gateway token from /home/node/.openclaw/openclaw.json inside openclaw container
GATEWAY_TOKEN="test-gateway-token-001"

scripts/register_real_openclaw_instance.sh \
  --instance-uuid "$OPENCLAW_INSTANCE_UUID" \
  --gateway-token "$GATEWAY_TOKEN" \
  --instance-url ws://openclaw:18789/ws

# Trigger any request through ws-router, then approve pairing:
docker compose exec -T openclaw openclaw devices approve --latest --json
```

### Step 4: Apply OpenClaw Runtime Defaults
```bash
./scripts/configure_openclaw_runtime.sh
```
This pins runtime to `pi` and provider API to `openai-responses`, which avoids:
- `Requested agent harness "codex" is not registered`
- SSRF blocks when using `openai-completions` against internal Docker DNS hostnames
Default model in this script is `lmstudio/qwen3.5-9b`.
The script also sets gateway token/auth defaults; after it runs, make sure DB mapping
uses the same token via `scripts/register_real_openclaw_instance.sh`.

## Verification

### 1. Check Services
```bash
./test_integration.sh
```

### 2. Check OpenClaw Logs
```bash
docker compose logs -f openclaw
```

### 3. Test Message Routing
```bash
./test_routing.sh
```

### 4. Monitor Router
```bash
docker compose logs -f ws-router
```

## Troubleshooting

### LM Studio Connection Issues
If nginx proxy can't reach LM Studio:
```bash
# Verify LM Studio is running on host
curl http://127.0.0.1:1234/v1/models

# Test from nginx container
docker compose exec lmstudio-proxy curl http://host.docker.internal:1234/v1/models
```

If you see `model_load_failed` in LM Studio logs, reduce model size/quantization
or increase available memory in LM Studio runtime settings.

### OpenClaw Connection Issues
If ws-router can't connect to OpenClaw:
```bash
# Check OpenClaw logs
docker compose -f docker-compose.prod.yml logs openclaw

# Verify OpenClaw is listening
docker compose -f docker-compose.prod.yml exec ws-router python - <<'PY'
import socket
s = socket.socket()
s.settimeout(2)
s.connect(("openclaw", 18789))
print("openclaw:18789 is reachable from ws-router")
PY

# Test WS connection
docker compose -f docker-compose.prod.yml logs --tail=100 ws-router
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
WS Router (ws-router:8060)
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

# Gateway URL (for OpenClaw to find ws-router)
GATEWAY_URL=http://ws-router:8060
```
