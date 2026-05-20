#!/usr/bin/env sh
set -eu

usage() {
  cat <<'EOF'
Usage:
  scripts/configure_openclaw_runtime.sh \
    [--compose-file docker-compose.prod.yml] \
    [--service openclaw] \
    [--container-name clawmux_openclaw] \
    [--model lmstudio/qwen3.5-9b] \
    [--provider lmstudio] \
    [--base-url http://lmstudio-proxy:1234/v1] \
    [--api openai-responses] \
    [--gateway-token test-gateway-token-001] \
    [--runtime-id pi]

Description:
  Idempotently applies OpenClaw runtime config for LM Studio and validates the result.
  This avoids the common failures:
    - Requested agent harness "codex" is not registered
    - SSRF blocked on openai-completions base URL
EOF
}

COMPOSE_FILE="docker-compose.prod.yml"
OPENCLAW_SERVICE="openclaw"
OPENCLAW_CONTAINER="clawmux_openclaw"
MODEL_ID="lmstudio/qwen3.5-9b"
PROVIDER_ID="lmstudio"
BASE_URL="http://lmstudio-proxy:1234/v1"
API_KIND="openai-responses"
GATEWAY_TOKEN="test-gateway-token-001"
RUNTIME_ID="pi"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --compose-file) COMPOSE_FILE="${2:-}"; shift 2 ;;
    --service) OPENCLAW_SERVICE="${2:-}"; shift 2 ;;
    --container-name) OPENCLAW_CONTAINER="${2:-}"; shift 2 ;;
    --model) MODEL_ID="${2:-}"; shift 2 ;;
    --provider) PROVIDER_ID="${2:-}"; shift 2 ;;
    --base-url) BASE_URL="${2:-}"; shift 2 ;;
    --api) API_KIND="${2:-}"; shift 2 ;;
    --gateway-token) GATEWAY_TOKEN="${2:-}"; shift 2 ;;
    --runtime-id) RUNTIME_ID="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required." >&2
  exit 1
fi

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

OPENCLAW_EXEC_MODE="compose"
if ! compose ps "$OPENCLAW_SERVICE" >/dev/null 2>&1; then
  OPENCLAW_EXEC_MODE="docker-exec"
  if ! docker ps --format '{{.Names}}' | grep -qx "$OPENCLAW_CONTAINER"; then
    echo "OpenClaw is not running." >&2
    echo "Checked:" >&2
    echo "  - docker compose -f $COMPOSE_FILE service '$OPENCLAW_SERVICE'" >&2
    echo "  - container '$OPENCLAW_CONTAINER'" >&2
    echo "Start stack first, then rerun this script." >&2
    exit 1
  fi
fi

openclaw_exec() {
  if [ "$OPENCLAW_EXEC_MODE" = "compose" ]; then
    compose exec -T "$OPENCLAW_SERVICE" "$@"
  else
    docker exec -i "$OPENCLAW_CONTAINER" "$@"
  fi
}

run_cfg() {
  key="$1"
  value="$2"
  openclaw_exec openclaw config set "$key" "$value" >/dev/null
}

run_cfg_json() {
  key="$1"
  json_value="$2"
  openclaw_exec openclaw config set "$key" "$json_value" --strict-json >/dev/null
}

bootstrap_provider_config() {
  model_short="$MODEL_ID"
  case "$model_short" in
    */*) model_short="${model_short#*/}" ;;
  esac

  patch_json="$(cat <<EOF
{
  "models": {
    "providers": {
      "$PROVIDER_ID": {
        "baseUrl": "$BASE_URL",
        "api": "$API_KIND",
        "agentRuntime": { "id": "$RUNTIME_ID" },
        "request": { "allowPrivateNetwork": true },
        "models": [
          {
            "id": "$model_short",
            "name": "$model_short",
            "reasoning": false,
            "input": ["text"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 200000,
            "maxTokens": 8192,
            "api": "$API_KIND"
          }
        ]
      }
    }
  }
}
EOF
)"
  printf '%s\n' "$patch_json" | openclaw_exec openclaw config patch --stdin >/dev/null
}

echo "Applying OpenClaw runtime config..."
# Ensure selected model/provider entry exists before setting provider keys.
openclaw_exec openclaw models set "$MODEL_ID" >/dev/null

if ! run_cfg "models.providers.${PROVIDER_ID}.baseUrl" "$BASE_URL"; then
  echo "Provider '$PROVIDER_ID' is missing schema-required fields; bootstrapping..."
  bootstrap_provider_config
  run_cfg "models.providers.${PROVIDER_ID}.baseUrl" "$BASE_URL"
fi
run_cfg "models.providers.${PROVIDER_ID}.api" "$API_KIND"
run_cfg "models.providers.${PROVIDER_ID}.agentRuntime.id" "$RUNTIME_ID"
run_cfg "models.providers.${PROVIDER_ID}.request.allowPrivateNetwork" "true"
run_cfg "gateway.auth.mode" "token"
run_cfg "gateway.auth.token" "$GATEWAY_TOKEN"
run_cfg_json "gateway.controlUi.allowedOrigins" "[\"http://localhost:18789\",\"http://127.0.0.1:18789\"]"

# Keep model-level default runtime pinned to a known registered harness.
run_cfg "agents.defaults.models[\"$MODEL_ID\"].agentRuntime.id" "$RUNTIME_ID"

echo "Result:"
openclaw_exec openclaw config get "models.providers.${PROVIDER_ID}.baseUrl" --json
openclaw_exec openclaw config get "models.providers.${PROVIDER_ID}.api" --json
openclaw_exec openclaw config get "models.providers.${PROVIDER_ID}.agentRuntime.id" --json
openclaw_exec openclaw config get "models.providers.${PROVIDER_ID}.request.allowPrivateNetwork" --json
openclaw_exec openclaw config get "gateway.auth.mode" --json
openclaw_exec openclaw config get "gateway.controlUi.allowedOrigins" --json
openclaw_exec openclaw config get "agents.defaults.models[\"$MODEL_ID\"].agentRuntime.id" --json
openclaw_exec openclaw models status

echo ""
echo "OpenClaw runtime config applied successfully."
echo "Use the same gateway token when registering clawmux instance:"
echo "  bash scripts/register_real_openclaw_instance.sh --instance-uuid <uuid> --gateway-token $GATEWAY_TOKEN --instance-url ws://openclaw:18789/ws"
