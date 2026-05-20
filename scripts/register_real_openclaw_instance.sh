#!/usr/bin/env sh
set -eu

usage() {
  cat <<'EOF'
Usage:
  scripts/register_real_openclaw_instance.sh \
    --instance-uuid <uuid> \
    --gateway-token <gateway_token> \
    [--instance-url ws://openclaw:18789/ws] \
    [--db-service postgres] \
    [--db-user router] \
    [--db-name ws_router]

Description:
  Generates a fresh device identity for ClawMux, then UPSERTs the record in
  table `instance` (instance_url + device credentials + gateway token).

  This is required for real OpenClaw when ClawMux logs:
    AUTH_DEVICE_TOKEN_MISMATCH
    DEVICE_AUTH_DEVICE_ID_MISMATCH
    PAIRING_REQUIRED

After running this script:
  1) Trigger any message through clawmux (POST /api/v1/trigger or Mattermost).
  2) Approve pending device in OpenClaw:
       docker compose exec -T openclaw openclaw devices approve --latest --json
EOF
}

INSTANCE_UUID=""
GATEWAY_TOKEN=""
INSTANCE_URL="ws://openclaw:18789/ws"
DB_SERVICE="postgres"
DB_USER="router"
DB_NAME="ws_router"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --instance-uuid) INSTANCE_UUID="${2:-}"; shift 2 ;;
    --gateway-token) GATEWAY_TOKEN="${2:-}"; shift 2 ;;
    --instance-url) INSTANCE_URL="${2:-}"; shift 2 ;;
    --db-service) DB_SERVICE="${2:-}"; shift 2 ;;
    --db-user) DB_USER="${2:-}"; shift 2 ;;
    --db-name) DB_NAME="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [ -z "$INSTANCE_UUID" ] || [ -z "$GATEWAY_TOKEN" ]; then
  echo "Missing required arguments." >&2
  usage
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required." >&2
  exit 1
fi

if ! docker compose ps "$DB_SERVICE" >/dev/null 2>&1; then
  echo "Service '$DB_SERVICE' not found. Start stack first: docker compose up -d" >&2
  exit 1
fi

CREDS_LINES="$(docker compose exec -T clawmux python - <<'PY'
import base64
import hashlib
import secrets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

private_key = Ed25519PrivateKey.generate()
public_key = private_key.public_key()

pub_raw = public_key.public_bytes(
    serialization.Encoding.Raw,
    serialization.PublicFormat.Raw,
)
priv_raw = private_key.private_bytes(
    serialization.Encoding.Raw,
    serialization.PrivateFormat.Raw,
    serialization.NoEncryption(),
)

print(f"device_id={hashlib.sha256(pub_raw).hexdigest()}")
print(f"public_key_b64={b64u(pub_raw)}")
print(f"private_key_b64={b64u(priv_raw)}")
print(f"device_token={b64u(secrets.token_bytes(32))}")
PY
)"

DEVICE_ID="$(printf '%s\n' "$CREDS_LINES" | sed -n 's/^device_id=//p')"
PUBLIC_KEY_B64="$(printf '%s\n' "$CREDS_LINES" | sed -n 's/^public_key_b64=//p')"
PRIVATE_KEY_B64="$(printf '%s\n' "$CREDS_LINES" | sed -n 's/^private_key_b64=//p')"
DEVICE_TOKEN="$(printf '%s\n' "$CREDS_LINES" | sed -n 's/^device_token=//p')"

if [ -z "$DEVICE_ID" ] || [ -z "$PUBLIC_KEY_B64" ] || [ -z "$PRIVATE_KEY_B64" ] || [ -z "$DEVICE_TOKEN" ]; then
  echo "Failed to generate credentials from clawmux container." >&2
  exit 1
fi

docker compose exec -T "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" <<EOF
\set ON_ERROR_STOP on
INSERT INTO instance (
  instance_uuid,
  instance_url,
  device_id,
  public_key_b64,
  private_key_b64,
  device_token,
  gateway_token
) VALUES (
  '$INSTANCE_UUID',
  '$INSTANCE_URL',
  '$DEVICE_ID',
  '$PUBLIC_KEY_B64',
  '$PRIVATE_KEY_B64',
  '$DEVICE_TOKEN',
  '$GATEWAY_TOKEN'
)
ON CONFLICT (instance_uuid) DO UPDATE SET
  instance_url = EXCLUDED.instance_url,
  device_id = EXCLUDED.device_id,
  public_key_b64 = EXCLUDED.public_key_b64,
  private_key_b64 = EXCLUDED.private_key_b64,
  device_token = EXCLUDED.device_token,
  gateway_token = EXCLUDED.gateway_token;
EOF

echo "Instance credentials registered in ws_router DB:"
echo "  instance_uuid=$INSTANCE_UUID"
echo "  instance_url=$INSTANCE_URL"
echo "  device_id=$DEVICE_ID"
echo ""
echo "Next steps:"
echo "  1) Trigger any message through clawmux."
echo "  2) Approve pending pairing in OpenClaw:"
echo "     docker compose exec -T openclaw openclaw devices approve --latest --json"
