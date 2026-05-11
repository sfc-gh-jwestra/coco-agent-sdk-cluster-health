#!/usr/bin/env bash
# =============================================================================
# Cluster Health Agent — Deployment Script
# =============================================================================
# Builds, pushes, and deploys the service to Snowpark Container Services.
#
# Usage:
#   ./deploy/deploy.sh                  # Full deploy (build + push + create service)
#   ./deploy/deploy.sh --skip-build     # Skip Docker build
#   ./deploy/deploy.sh --skip-push      # Skip registry push
#   ./deploy/deploy.sh --update         # ALTER existing service (re-deploy)
#
# Requires: .env file in project root (copy from .env.example)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Parse flags ---
SKIP_BUILD=false
SKIP_PUSH=false
UPDATE_MODE=false

for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=true ;;
    --skip-push)  SKIP_PUSH=true ;;
    --update)     UPDATE_MODE=true ;;
    --help|-h)
      echo "Usage: ./deploy/deploy.sh [--skip-build] [--skip-push] [--update]"
      echo ""
      echo "  --skip-build   Skip the Docker image build step"
      echo "  --skip-push    Skip registry login, tag, and push"
      echo "  --update       ALTER existing service instead of CREATE"
      exit 0
      ;;
    *) echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

# --- Load .env ---
ENV_FILE="$PROJECT_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env file not found at $ENV_FILE"
  echo "       Copy .env.example to .env and fill in your values."
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

# --- Validate required vars ---
REQUIRED_VARS=(SNOWFLAKE_ACCOUNT SNOWFLAKE_HOST SNOWFLAKE_DATABASE SNOWFLAKE_SCHEMA SNOWFLAKE_WAREHOUSE SNOW_CONNECTION NOTIFICATION_INTEGRATION REGISTRY_URL)
for var in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: Required variable $var is not set in .env"
    exit 1
  fi
done

# --- Auto-generate TOKEN_SECRET_KEY if empty ---
if [[ -z "${TOKEN_SECRET_KEY:-}" ]]; then
  TOKEN_SECRET_KEY=$(openssl rand -hex 32)
  echo "Generated TOKEN_SECRET_KEY (save this to your .env):"
  echo "  TOKEN_SECRET_KEY=$TOKEN_SECRET_KEY"
  echo ""
fi

# --- Derived values ---
# REGISTRY_URL must be set in .env (get it from SHOW IMAGE REPOSITORIES)
if [[ -z "${REGISTRY_URL:-}" ]]; then
  echo "ERROR: REGISTRY_URL is not set in .env"
  echo "       Get it from: SHOW IMAGE REPOSITORIES IN SCHEMA SPORTSBOOK_DW.WAGERS"
  echo "       Use the hostname portion, e.g.: myorg-myaccount.registry.snowflakecomputing.com"
  exit 1
fi
IMAGE_PATH="${REGISTRY_URL}/sportsbook_dw/wagers/cluster_health_repo/cluster-health-agent:latest"
TOKEN_TTL_HOURS="${TOKEN_TTL_HOURS:-24}"
SERVICE_BASE_URL="${SERVICE_BASE_URL:-}"

# --- Step 1: Build Docker image ---
if [[ "$SKIP_BUILD" == false ]]; then
  echo "==> Building Docker image..."
  docker build --platform linux/amd64 -t cluster-health-agent:latest -f "$SCRIPT_DIR/Dockerfile" "$PROJECT_DIR"
  echo "    Build complete."
else
  echo "==> Skipping Docker build (--skip-build)"
fi

# --- Step 2: Push to Snowflake Image Registry ---
if [[ "$SKIP_PUSH" == false ]]; then
  echo "==> Logging into Snowflake image registry..."
  snow spcs image-registry login --connection "$SNOW_CONNECTION"

  echo "==> Tagging image..."
  docker tag cluster-health-agent:latest "$IMAGE_PATH"

  echo "==> Pushing image to $IMAGE_PATH..."
  docker push "$IMAGE_PATH"
  echo "    Push complete."
else
  echo "==> Skipping registry push (--skip-push)"
fi

# --- Step 3: Render service_spec.yaml from template ---
echo "==> Rendering service_spec.yaml from template..."
sed -e "s|\${SNOWFLAKE_DATABASE}|${SNOWFLAKE_DATABASE}|g" \
    -e "s|\${SNOWFLAKE_SCHEMA}|${SNOWFLAKE_SCHEMA}|g" \
    -e "s|\${SNOWFLAKE_WAREHOUSE}|${SNOWFLAKE_WAREHOUSE}|g" \
    -e "s|\${NOTIFICATION_INTEGRATION}|${NOTIFICATION_INTEGRATION}|g" \
    -e "s|\${SERVICE_BASE_URL}|${SERVICE_BASE_URL}|g" \
    -e "s|\${TOKEN_SECRET_KEY}|${TOKEN_SECRET_KEY}|g" \
    -e "s|\${TOKEN_TTL_HOURS}|${TOKEN_TTL_HOURS}|g" \
    "$SCRIPT_DIR/service_spec.yaml.tpl" > "$SCRIPT_DIR/service_spec.yaml"
echo "    Generated $SCRIPT_DIR/service_spec.yaml"

# --- Step 4: Upload spec and create/alter service ---
echo "==> Uploading service_spec.yaml to stage..."
snow sql -c "$SNOW_CONNECTION" --role ACCOUNTADMIN -q "PUT file://$SCRIPT_DIR/service_spec.yaml @SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"

if [[ "$UPDATE_MODE" == true ]]; then
  echo "==> Altering existing service..."
  snow sql -c "$SNOW_CONNECTION" --role ACCOUNTADMIN -q "ALTER SERVICE SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_SERVICE
    FROM @SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_STAGE
    SPECIFICATION_FILE = 'service_spec.yaml';"
else
  echo "==> Creating service..."
  snow sql -c "$SNOW_CONNECTION" --role ACCOUNTADMIN -q "CREATE SERVICE IF NOT EXISTS SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_SERVICE
    IN COMPUTE POOL CLUSTER_HEALTH_POOL
    FROM @SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_STAGE
    SPECIFICATION_FILE = 'service_spec.yaml'
    MIN_INSTANCES = 1
    MAX_INSTANCES = 1;"
fi

echo ""
echo "==> Deployment complete!"
echo ""

if [[ "$UPDATE_MODE" == true ]]; then
  echo "Next steps:"
  echo "  1. Verify the service restarted:"
  echo "     snow sql -c $SNOW_CONNECTION -q \"SELECT SYSTEM\$GET_SERVICE_STATUS('SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_SERVICE');\""

  echo ""
  echo "==> Creating service function and scheduled task..."
  snow sql -c "$SNOW_CONNECTION" --role ACCOUNTADMIN -q "CREATE FUNCTION IF NOT EXISTS SPORTSBOOK_DW.WAGERS.TRIGGER_HEALTH_CHECK()
    RETURNS VARCHAR
    SERVICE = SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_SERVICE
    ENDPOINT = 'cluster-health-endpoint'
    AS '/health-check';"

  snow sql -c "$SNOW_CONNECTION" --role ACCOUNTADMIN -q "CREATE TASK IF NOT EXISTS SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_CHECK_TASK
    WAREHOUSE = CLUSTER_HEALTH_WH
    SCHEDULE = '5 MINUTE'
    AS SELECT SPORTSBOOK_DW.WAGERS.TRIGGER_HEALTH_CHECK();"

  snow sql -c "$SNOW_CONNECTION" --role ACCOUNTADMIN -q "ALTER TASK IF EXISTS SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_CHECK_TASK RESUME;"

  echo "    Service function and scheduled task ready."
else
  echo "Next steps:"
  echo "  1. Check service status (wait for READY):"
  echo "     snow sql -c $SNOW_CONNECTION -q \"SELECT SYSTEM\$GET_SERVICE_STATUS('SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_SERVICE');\""
  echo "  2. Get the ingress URL:"
  echo "     snow sql -c $SNOW_CONNECTION -q \"SHOW ENDPOINTS IN SERVICE SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_SERVICE;\""
  echo "  3. Set SERVICE_BASE_URL in .env to the ingress URL, then re-run:"
  echo "     ./deploy/deploy.sh --skip-build --skip-push --update"
fi
