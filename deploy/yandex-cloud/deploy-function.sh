#!/usr/bin/env bash
set -euo pipefail

: "${YC_FOLDER_ID:?Set YC_FOLDER_ID}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

FUNCTION_NAME="${FUNCTION_NAME:-supplier-radar-pushkino}"
TRIGGER_NAME="${TRIGGER_NAME:-supplier-radar-hourly}"
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-supplier-radar-runtime}"
LOCKBOX_SECRET_NAME="${LOCKBOX_SECRET_NAME:-supplier-radar-runtime}"
MEMORY="${MEMORY:-1GB}"
EXECUTION_TIMEOUT="${EXECUTION_TIMEOUT:-3300s}"
CRON="${CRON:-5 * ? * * *}"
SUPPLIER_SHEET_ID="${SUPPLIER_SHEET_ID:-1oP6pury0HB_M8ajF6--l2PF25m2alB5tjwP7I-YFg0M}"
SUPPLIER_CANDIDATE_MIN_SCORE="${SUPPLIER_CANDIDATE_MIN_SCORE:-40}"

for c in yc jq; do
  command -v "$c" >/dev/null || { echo "ERROR: $c is required" >&2; exit 2; }
done

yc config set folder-id "$YC_FOLDER_ID" >/dev/null
yc resource-manager folder get "$YC_FOLDER_ID" >/dev/null

ensure_folder_role() {
  local sa="$1" role="$2"
  if ! yc resource-manager folder list-access-bindings "$YC_FOLDER_ID" --format json \
      | jq -e --arg m "serviceAccount:${sa}" --arg r "$role" \
        '.[] | select(.roleId==$r) | .subjects[]? | select((.type+":"+.id)==$m)' >/dev/null; then
    yc resource-manager folder add-access-binding "$YC_FOLDER_ID" \
      --role "$role" --subject "serviceAccount:$sa" >/dev/null
  fi
}

if ! yc iam service-account get --name "$RUNTIME_SA_NAME" >/dev/null 2>&1; then
  yc iam service-account create --name "$RUNTIME_SA_NAME" >/dev/null
fi
RUNTIME_SA_ID="$(yc iam service-account get --name "$RUNTIME_SA_NAME" --format json | jq -r .id)"
ensure_folder_role "$RUNTIME_SA_ID" functions.functionInvoker

if ! yc lockbox secret get --name "$LOCKBOX_SECRET_NAME" >/dev/null 2>&1; then
  yc lockbox secret create --name "$LOCKBOX_SECRET_NAME" --description "Supplier Radar runtime secrets" >/dev/null
fi
LOCKBOX_SECRET_ID="$(yc lockbox secret get --name "$LOCKBOX_SECRET_NAME" --format json | jq -r .id)"
yc lockbox secret add-access-binding \
  --id "$LOCKBOX_SECRET_ID" \
  --role lockbox.payloadViewer \
  --subject "serviceAccount:$RUNTIME_SA_ID" >/dev/null 2>&1 || true

LOCKBOX_VERSION_ID="${YANDEX_LOCKBOX_SECRET_VERSION_ID:-$(
  yc lockbox secret list-versions --id "$LOCKBOX_SECRET_ID" --format json \
    | jq -r '[.[] | select((.status // "ACTIVE") == "ACTIVE")][0].id // .[0].id // empty'
)}"
[[ -n "$LOCKBOX_VERSION_ID" ]] || {
  echo "ERROR: Lockbox has no active version." >&2
  echo "Required keys: YANDEX_SEARCH_API_KEY and GOOGLE_SERVICE_ACCOUNT_JSON" >&2
  exit 3
}
KEYS="$(yc lockbox payload get --id "$LOCKBOX_SECRET_ID" --version-id "$LOCKBOX_VERSION_ID" --format json | jq -r '.entries[]?.key')"
for required in YANDEX_SEARCH_API_KEY GOOGLE_SERVICE_ACCOUNT_JSON; do
  grep -qx "$required" <<<"$KEYS" || {
    echo "ERROR: Lockbox missing key: $required" >&2
    exit 4
  }
done

if ! yc serverless function get --name "$FUNCTION_NAME" >/dev/null 2>&1; then
  yc serverless function create --name "$FUNCTION_NAME" --description "Hourly Pushkino supplier radar" >/dev/null
fi
FUNCTION_ID="$(yc serverless function get --name "$FUNCTION_NAME" --format json | jq -r .id)"

PACKAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$PACKAGE_DIR"' EXIT
cp -R src config "$PACKAGE_DIR"/
cp function_handler.py "$PACKAGE_DIR"/
cat >"$PACKAGE_DIR/requirements.txt" <<'REQ'
httpx>=0.27
beautifulsoup4>=4.12
yandex-ai-studio-sdk>=0.16
google-auth[requests]>=2.35
REQ

echo "[1/4] deploy Cloud Function version"
yc serverless function version create \
  --function-id "$FUNCTION_ID" \
  --runtime python312 \
  --entrypoint function_handler.handler \
  --memory "$MEMORY" \
  --execution-timeout "$EXECUTION_TIMEOUT" \
  --concurrency 1 \
  --service-account-id "$RUNTIME_SA_ID" \
  --environment "YANDEX_FOLDER_ID=${YC_FOLDER_ID},SUPPLIER_RADAR_CONFIG=config/pushkino.json,SUPPLIER_SHEET_ID=${SUPPLIER_SHEET_ID},SUPPLIER_CANDIDATE_MIN_SCORE=${SUPPLIER_CANDIDATE_MIN_SCORE},SUPPLIER_RADAR_HISTORY=/tmp/supplier-history.json,SUPPLIER_RADAR_CHECKPOINT_DIR=/tmp/checkpoints" \
  --secret "environment-variable=YANDEX_SEARCH_API_KEY,id=${LOCKBOX_SECRET_ID},version-id=${LOCKBOX_VERSION_ID},key=YANDEX_SEARCH_API_KEY" \
  --secret "environment-variable=GOOGLE_SERVICE_ACCOUNT_JSON,id=${LOCKBOX_SECRET_ID},version-id=${LOCKBOX_VERSION_ID},key=GOOGLE_SERVICE_ACCOUNT_JSON" \
  --source-path "$PACKAGE_DIR" >/dev/null

echo "[2/4] recreate hourly Timer"
if yc serverless trigger get --name "$TRIGGER_NAME" >/dev/null 2>&1; then
  yc serverless trigger delete --name "$TRIGGER_NAME" >/dev/null
fi
yc serverless trigger create timer \
  --name "$TRIGGER_NAME" \
  --cron-expression "$CRON" \
  --payload '{"source":"supplier-radar-hourly"}' \
  --invoke-function-id "$FUNCTION_ID" \
  --invoke-function-service-account-id "$RUNTIME_SA_ID" \
  --retry-attempts 1 \
  --retry-interval 30s >/dev/null
TRIGGER_ID="$(yc serverless trigger get --name "$TRIGGER_NAME" --format json | jq -r .id)"

echo "[3/4] verify resources"
yc serverless function get --id "$FUNCTION_ID" --format json | jq '{id,name,status}'
yc serverless trigger get --id "$TRIGGER_ID" --format json | jq '{id,name,status}'

if [[ "${SKIP_SMOKE:-0}" != "1" ]]; then
  echo "[4/4] invoke production smoke (this executes a real Supplier Radar run)"
  yc serverless function invoke "$FUNCTION_ID" -d '{"source":"manual-production-smoke"}'
else
  echo "[4/4] smoke skipped by SKIP_SMOKE=1"
fi

cat <<EOF
YANDEX_FUNCTION_AUTOPILOT_DEPLOYED
FUNCTION_ID=$FUNCTION_ID
TRIGGER_ID=$TRIGGER_ID
CRON_UTC=$CRON
EXECUTION_TIMEOUT=$EXECUTION_TIMEOUT
SHEET_ID=$SUPPLIER_SHEET_ID
LOCKBOX_SECRET_ID=$LOCKBOX_SECRET_ID
NEXT_PROOF=Sheet must show START -> SEARCH_BATCH_* -> ENRICH -> DEEP_SCAN -> FINISH, then 3 consecutive timer slots.
EOF
