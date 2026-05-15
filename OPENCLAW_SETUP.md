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
docker compose up -d --build
```

### Step 3: Configure Database
Once OpenClaw starts, it should register itself with the router. If not, manually update the mapping:

```bash
# Find the OpenClaw instance UUID from its logs
docker compose logs openclaw | grep instance_uuid

# Or use the hardcoded UUID from environment
OPENCLAW_INSTANCE_UUID="your-instance-uuid-here"

# Update the database
docker compose exec -T postgres psql -U router -d ws_router << 'EOF'
UPDATE instance
SET instance_url = 'ws://openclaw:19000'
WHERE instance_uuid = '2f99d082-71fb-4bf7-a4c5-cfeea78976c6';
EOF

docker compose restart ws-router
```

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

### OpenClaw Connection Issues
If ws-router can't connect to OpenClaw:
```bash
# Check OpenClaw logs
docker compose logs openclaw

# Verify OpenClaw is listening
docker compose exec openclaw netstat -tlnp | grep 19000

# Test WS connection
docker compose exec ws-router wscat -c ws://openclaw:19000
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
OpenClaw Instance (openclaw:19000)
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
