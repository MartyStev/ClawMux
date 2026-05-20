# Switch to Real OpenClaw with LM Studio

## 📋 Current configuration

The system is running with **Mock OpenClaw** for testing. To use a real OpenClaw with LM Studio, follow these steps:

## ✅ Requirements

- ✔️ LM Studio is running at `http://127.0.0.1:1234`
- ✔️ The model `qwen/qwen3.5-9b` is loaded in LM Studio
- ✔️ Docker is installed
- ✔️ Git is installed

## 🚀 Install real OpenClaw

### Step 1: Get the OpenClaw image

**Option A: Use the public image**
```bash
export OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:latest
./scripts/setup-openclaw.sh
```

**Option B: Build from local source**
```bash
export OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:latest
export OPENCLAW_SRC=/path/to/openclaw-source
./scripts/setup-openclaw.sh
```

If you already have a valid image, you can simply set `OPENCLAW_IMAGE` and run `docker compose -f docker-compose.prod.yml up -d`.

### Step 2: Verify that LM Studio is running

```bash
# Make sure LM Studio is listening on 127.0.0.1:1234
curl http://127.0.0.1:1234/v1/models | jq .

# You should see the qwen model in the response
```

### Step 3: Stop the current system

```bash
docker compose down --remove-orphans
```

### Step 4: Start with real OpenClaw

```bash
# Use the production compose file
docker compose -f docker-compose.prod.yml up -d

# Or use --build if you need to rebuild images
docker compose -f docker-compose.prod.yml up -d --build
```

The `docker-compose.prod.yml` adds a persistent volume `openclaw_state:/home/node/.openclaw`, so OpenClaw configuration and identity are not lost after recreate.

### Step 5: Update the database configuration

```bash
# Example UUID used in this project:
OPENCLAW_INSTANCE_UUID="2f99d082-71fb-4bf7-a4c5-cfeea78976c6"

# Gateway token (see /home/node/.openclaw/openclaw.json inside openclaw)
GATEWAY_TOKEN="test-gateway-token-001"

# Important: register not only instance_url, but also device credentials:
scripts/register_real_openclaw_instance.sh \
  --instance-uuid "$OPENCLAW_INSTANCE_UUID" \
  --gateway-token "$GATEWAY_TOKEN" \
  --instance-url ws://openclaw:18789/ws

# Generate pending pairing (any trigger/message), then:
docker compose -f docker-compose.prod.yml exec -T openclaw \
  openclaw devices approve --latest --json
```

### Step 6: Lock in OpenClaw runtime settings
```bash
./scripts/configure_openclaw_runtime.sh
```

The script fixes runtime `pi`, provider `lmstudio`, and API `openai-responses`.
Default model: `lmstudio/qwen3.5-9b`.
After running the script, use the same `gateway-token` in `scripts/register_real_openclaw_instance.sh`, then confirm pairing.
This resolves common errors:
- `Requested agent harness "codex" is not registered`
- `SsrFBlockedError` for `openai-completions` and `lmstudio-proxy`

## 🧪 Testing

### Check system health
```bash
./scripts/test_integration.sh
```

### Test routing
```bash
./scripts/test_routing.sh
```

### Monitor logs
```bash
# All services
docker compose -f docker-compose.prod.yml logs -f

# Only OpenClaw
docker compose -f docker-compose.prod.yml logs -f openclaw

# Only ClawMux
docker compose -f docker-compose.prod.yml logs -f clawmux

# Only LM Studio proxy
docker compose -f docker-compose.prod.yml logs -f lmstudio-proxy
```

## 🔧 Configuration

Environment variables in `docker-compose.prod.yml`:

```yaml
# LLM Configuration
LLM_PROVIDER: lmstudio        # LLM provider
LLM_BASE_URL: http://lmstudio-proxy:1234/v1  # Base URL
LLM_MODEL: qwen/qwen3.5-9b   # Model (change if you use another)

# Instance Configuration
INSTANCE_NAME: ClawMux-Main   # Instance name
DEVICE_ID: clawmux-device-001 # Device ID
GATEWAY_URL: http://clawmux:8060  # Router URL
```

## 🔄 Switching between mock and real

### To mock (for testing)
```bash
# Make sure the main compose file is used
docker compose down
docker compose up -d
```

### To real (production)
```bash
docker compose down
docker compose -f docker-compose.prod.yml up -d
```

## 🐛 Troubleshooting

### Problem: OpenClaw does not connect to LM Studio

```bash
# Check LM Studio availability on the host
curl http://127.0.0.1:1234/v1/models

# Check inside the nginx container
docker compose -f docker-compose.prod.yml exec lmstudio-proxy \
  curl http://host.docker.internal:1234/v1/models

# Check OpenClaw logs
docker compose -f docker-compose.prod.yml logs openclaw | grep -i "llm\|error"
```

If LM Studio logs contain `model_load_failed` / `insufficient system resources`, choose a lighter model or reduce requirements (quant/context/memory).

### Problem: ClawMux cannot connect to OpenClaw

```bash
# Check that OpenClaw is listening on the port
docker compose -f docker-compose.prod.yml exec openclaw \
  sh -lc "echo 'use clawmux-side probe instead'"

# Check logs
docker compose -f docker-compose.prod.yml logs openclaw

# Test WS connection
docker compose -f docker-compose.prod.yml exec clawmux \
  python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('openclaw',18789)); print('OK openclaw:18789')"
```

If you get `Requested agent harness "codex" is not registered`, run:
```bash
./scripts/configure_openclaw_runtime.sh
```

### Problem: Model is not loaded in LM Studio

```bash
# Make sure the model is loaded
curl http://127.0.0.1:1234/v1/models | jq '.data[] | .id'

# If not, load it in the LM Studio GUI:
# 1. Open http://127.0.0.1:1234
# 2. Search for: qwen/qwen3.5-9b
# 3. Click Load
```

## 📊 Architecture with real OpenClaw

```
┌─────────────────┐
│  Mattermost     │
└────────┬────────┘
         │ WebSocket
         ▼
┌─────────────────┐
│   ClawMux       │ :8060
│  (clawmux)      │
└────────┬────────┘
         │ WebSocket
         ▼
┌─────────────────┐
│  OpenClaw       │ :19000
│  Instance       │
└────────┬────────┘
         │ HTTP API (/v1)
         ▼
┌─────────────────┐
│ nginx Proxy     │ :1234
│(lmstudio-proxy) │
└────────┬────────┘
         │ HTTP
         ▼
┌─────────────────┐
│  LM Studio      │ 127.0.0.1:1234
│ (Host machine)  │
└────────┬────────┘
         │ API calls
         ▼
┌─────────────────┐
│  qwen/qwen3.5-9b│
│  (Model)        │
└─────────────────┘
```

## 📝 Reference commands

```bash
# View all services
docker compose -f docker-compose.prod.yml ps

# Stop only OpenClaw
docker compose -f docker-compose.prod.yml stop openclaw

# Restart ClawMux
docker compose -f docker-compose.prod.yml restart clawmux

# View resource usage
docker compose -f docker-compose.prod.yml stats

# Remove all data and start over
docker compose -f docker-compose.prod.yml down -v
```

## ✨ Done!

The system is configured and ready to use with real OpenClaw and LM Studio.

For help, check logs:
```bash
docker compose -f docker-compose.prod.yml logs --tail=50
```
