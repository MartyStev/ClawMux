#!/bin/bash
# Quick setup script for real OpenClaw

set -e

echo "════════════════════════════════════════════════════════════════"
echo "  Setting up real OpenClaw instance"
echo "════════════════════════════════════════════════════════════════"
echo ""

OPENCLAW_IMAGE=${OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:latest}
echo "Using OpenClaw image: $OPENCLAW_IMAGE"

echo ""
# If local source is provided, build from source.
if [ -n "$OPENCLAW_SRC" ] && [ -d "$OPENCLAW_SRC" ]; then
    echo "✅ Found local OpenClaw source: $OPENCLAW_SRC"
    docker build -t "$OPENCLAW_IMAGE" "$OPENCLAW_SRC"
    echo ""
    echo "✅ OpenClaw image built: $OPENCLAW_IMAGE"
    exit 0
fi

# If a common source directory exists, build from there.
if [ -d "$HOME/openclaw" ] || [ -d "$HOME/openclaw-core" ]; then
    OPENCLAW_SRC=$(find "$HOME" -maxdepth 2 -type d \( -name "openclaw-core" -o -name "openclaw" \) | head -1)
    if [ -n "$OPENCLAW_SRC" ]; then
        echo "✅ Found OpenClaw source directory: $OPENCLAW_SRC"
        docker build -t "$OPENCLAW_IMAGE" "$OPENCLAW_SRC"
        echo ""
        echo "✅ OpenClaw image built: $OPENCLAW_IMAGE"
        exit 0
    fi
fi

# Otherwise attempt to pull the default registry image.
echo "⚠️  OpenClaw source not found locally"
echo "Attempting to pull image from registry..."
if docker pull "$OPENCLAW_IMAGE"; then
    echo ""
    echo "✅ Pulled OpenClaw image: $OPENCLAW_IMAGE"
    exit 0
fi

cat <<'EOF'
❌ Could not obtain an OpenClaw image automatically.

Options:
  1) If you already have a local OpenClaw source tree, set:
       OPENCLAW_SRC=/path/to/openclaw-source
     then rerun this script.

  2) If you want to use a custom OpenClaw image, set:
       OPENCLAW_IMAGE=your-registry/openclaw:tag
     then rerun this script.

  3) If you know the upstream repository URL, clone it manually and build:
       git clone <repo-url>
       docker build -t "$OPENCLAW_IMAGE" <repo-path>

If you do not have a valid source repository path, use the registry image:
  ghcr.io/openclaw/openclaw:latest
EOF

exit 1
