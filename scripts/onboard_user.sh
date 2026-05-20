#!/usr/bin/env sh
set -eu

usage() {
  cat <<'EOF'
Usage:
  scripts/onboard_user.sh \
    --app-user-id <internal_user_id> \
    --external-user-id <external_user_id> \
    --provider-user-id <provider_user_id> \
    --instance-uuid <instance_uuid> \
    [--provider mattermost] \
    [--role <role>] \
    [--db-user router] \
    [--db-name ws_router] \
    [--db-service postgres] \
    [--router-service clawmux] \
    [--no-restart]

Description:
  Creates/updates app_user + user_identity and binds the user to an existing
  OpenClaw instance in user_instance (1 user = 1 instance).

  By default, runs SQL through:
    docker compose exec -T <db-service> psql -U <db-user> -d <db-name>
EOF
}

APP_USER_ID=""
EXTERNAL_USER_ID=""
PROVIDER_USER_ID=""
INSTANCE_UUID=""
PROVIDER="mattermost"
ROLE=""
DB_USER="router"
DB_NAME="ws_router"
DB_SERVICE="postgres"
ROUTER_SERVICE="clawmux"
RESTART_ROUTER="1"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --app-user-id) APP_USER_ID="${2:-}"; shift 2 ;;
    --external-user-id) EXTERNAL_USER_ID="${2:-}"; shift 2 ;;
    --provider-user-id) PROVIDER_USER_ID="${2:-}"; shift 2 ;;
    --instance-uuid) INSTANCE_UUID="${2:-}"; shift 2 ;;
    --provider) PROVIDER="${2:-}"; shift 2 ;;
    --role) ROLE="${2:-}"; shift 2 ;;
    --db-user) DB_USER="${2:-}"; shift 2 ;;
    --db-name) DB_NAME="${2:-}"; shift 2 ;;
    --db-service) DB_SERVICE="${2:-}"; shift 2 ;;
    --router-service) ROUTER_SERVICE="${2:-}"; shift 2 ;;
    --no-restart) RESTART_ROUTER="0"; shift 1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [ -z "$APP_USER_ID" ] || [ -z "$EXTERNAL_USER_ID" ] || [ -z "$PROVIDER_USER_ID" ] || [ -z "$INSTANCE_UUID" ]; then
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

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SQL_FILE="$SCRIPT_DIR/onboard_user.sql"

docker compose exec -T "$DB_SERVICE" \
  psql -U "$DB_USER" -d "$DB_NAME" \
    -v app_user_id="$APP_USER_ID" \
    -v external_user_id="$EXTERNAL_USER_ID" \
    -v provider="$PROVIDER" \
    -v provider_user_id="$PROVIDER_USER_ID" \
    -v instance_uuid="$INSTANCE_UUID" \
    -v role="$ROLE" \
    -f - < "$SQL_FILE"

echo "Onboarding completed:"
echo "  app_user_id=$APP_USER_ID"
echo "  external_user_id=$EXTERNAL_USER_ID"
echo "  provider=$PROVIDER"
echo "  provider_user_id=$PROVIDER_USER_ID"
echo "  instance_uuid=$INSTANCE_UUID"

if [ "$RESTART_ROUTER" = "1" ]; then
  echo "Restarting $ROUTER_SERVICE to invalidate connection cache..."
  docker compose restart "$ROUTER_SERVICE" >/dev/null
fi
