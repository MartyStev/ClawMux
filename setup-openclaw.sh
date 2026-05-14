#!/bin/bash
# Quick setup script for real OpenClaw

echo "════════════════════════════════════════════════════════════════"
echo "  Setting up real OpenClaw instance"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Option 1: Check if user has OpenClaw source
if [ -d "$HOME/openclaw" ] || [ -d "$HOME/openclaw-core" ]; then
    echo "✅ Found OpenClaw source directory"
    OPENCLAW_SRC=$(find ~/ -maxdepth 2 -name "openclaw-core" -o -name "openclaw" -type d 2>/dev/null | head -1)
    echo "Building from: $OPENCLAW_SRC"
    docker build -t openclawai/openclaw:latest "$OPENCLAW_SRC"
    echo ""
    echo "✅ OpenClaw image built!"
    exit 0
fi

# Option 2: Clone from GitHub
echo "OpenClaw source not found. Cloning from GitHub..."
echo ""
TEMP_DIR=$(mktemp -d)
echo "Clone location: $TEMP_DIR"
git clone https://github.com/openclawai/openclaw-core.git "$TEMP_DIR/openclaw-core"

if [ $? -eq 0 ]; then
    echo "✅ Cloned successfully"
    echo ""
    echo "Building OpenClaw image..."
    docker build -t openclawai/openclaw:latest "$TEMP_DIR/openclaw-core"
    
    if [ $? -eq 0 ]; then
        echo "✅ OpenClaw image built successfully!"
        echo ""
        echo "You can now run:"
        echo "  docker compose up -d"
    else
        echo "❌ Build failed"
        exit 1
    fi
else
    echo "❌ Clone failed"
    echo ""
    echo "Manual setup:"
    echo "  1. Visit: https://github.com/openclawai/openclaw-core"
    echo "  2. Clone the repository"
    echo "  3. Run: docker build -t openclawai/openclaw:latest ."
    exit 1
fi

# Cleanup
rm -rf "$TEMP_DIR"
