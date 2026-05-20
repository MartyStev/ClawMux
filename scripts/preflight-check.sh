#!/bin/bash

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  ClawMux with Real OpenClaw + LM Studio Setup                   ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Check 1: LM Studio
echo "1️⃣  Checking LM Studio on http://127.0.0.1:1234..."
if curl -s http://127.0.0.1:1234/v1/models | grep -q "qwen"; then
    echo "   ✅ LM Studio is running with qwen model loaded"
else
    echo "   ❌ LM Studio not responding or model not loaded"
    echo "   Please start LM Studio with qwen/qwen3.5-9b model on port 1234"
    exit 1
fi

# Check 2: Docker
echo ""
echo "2️⃣  Checking Docker..."
if ! command -v docker &> /dev/null; then
    echo "   ❌ Docker not found"
    exit 1
fi
echo "   ✅ Docker is available"

# Check 3: OpenClaw image
echo ""
echo "3️⃣  Checking OpenClaw Docker image..."
OPENCLAW_IMAGE=${OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:latest}
echo "   ℹ️  Expected OpenClaw image: $OPENCLAW_IMAGE"
if docker image inspect "$OPENCLAW_IMAGE" &> /dev/null; then
    echo "   ✅ OpenClaw image found locally"
else
    echo "   ⚠️  OpenClaw image not found locally"
    echo "   Attempting to pull from registry..."
    if docker pull "$OPENCLAW_IMAGE" 2>&1 | grep -q "Digest:"; then
        echo "   ✅ OpenClaw image pulled successfully"
    else
        echo "   ⚠️  Could not pull OpenClaw image from registry"
        echo "   Use one of these options:"
        echo "   1) Set OPENCLAW_IMAGE to a valid image, e.g. ghcr.io/openclaw/openclaw:latest"
        echo "   2) If you have a local OpenClaw source tree, set OPENCLAW_SRC=/path/to/source and rerun this script"
    fi
fi

# Check 4: Port availability
echo ""
echo "4️⃣  Checking port availability..."
declare -a PORTS=(8065 8060 19000 5432)
for port in "${PORTS[@]}"; do
    if nc -z 127.0.0.1 $port 2>/dev/null; then
        echo "   ⚠️  Port $port is already in use"
    else
        echo "   ✅ Port $port is available"
    fi
done
echo "   ℹ️  Port 1234 should be occupied by LM Studio on host (this is expected)"

# Check 5: .env
echo ""
echo "5️⃣  Checking configuration..."
if [ -f .env ]; then
    echo "   ✅ .env file exists"
else
    echo "   ⚠️  .env file not found"
    echo "   Creating from .env.example..."
    cp .env.example .env
    echo "   ✅ .env created"
fi

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Ready to start! Run:                                           ║"
echo "║  docker compose down --remove-orphans                           ║"
echo "║  docker compose -f docker-compose.prod.yml up -d --build        ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "Configuration summary:"
echo "  - LM Studio: http://127.0.0.1:1234"
echo "  - Model: qwen/qwen3.5-9b"
echo "  - Mattermost: http://localhost:8065"
echo "  - ClawMux: http://localhost:8060"
echo "  - OpenClaw: ws://localhost:19000"
echo ""
echo "After startup, run tests:"
echo "  ./scripts/test_integration.sh   # Full system test"
echo "  ./scripts/test_routing.sh       # Message routing test"
echo "  docker compose logs -f clawmux  # Monitor logs"
echo ""
echo "For real OpenClaw stack, apply runtime defaults once:"
echo "  ./scripts/configure_openclaw_runtime.sh"
