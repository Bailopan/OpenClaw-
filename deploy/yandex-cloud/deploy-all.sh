#!/usr/bin/env bash
set -euo pipefail

: "${YC_FOLDER_ID:?Set YC_FOLDER_ID}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

for c in yc docker jq curl; do
  command -v "$c" >/dev/null || { echo "ERROR: $c is required" >&2; exit 2; }
done

yc config set folder-id "$YC_FOLDER_ID" >/dev/null
yc resource-manager folder get "$YC_FOLDER_ID" >/dev/null

echo "[1/5] bootstrap Yandex resources"
BOOTSTRAP_OUT="$(./deploy/yandex-cloud/bootstrap.sh)"
printf '%s\n' "$BOOTSTRAP_OUT"

LOCKBOX_ID="${YANDEX_LOCKBOX_SECRET_ID:-$(printf '%s\n' "$BOOTSTRAP_OUT" | awk -F= '/^YANDEX_LOCKBOX_SECRET_ID=/{print $2; exit}')}"
[[ -n "$LOCKBOX_ID" ]] || { echo "ERROR: cannot resolve Lockbox secret id" >&2; exit 3; }
export YANDEX_LOCKBOX_SECRET_ID="$LOCKBOX_ID"

ACTIVE_VERSION_ID="$(yc lockbox secret list-versions --id "$LOCKBOX_ID" --format json | jq -r '[.[] | select((.status // "ACTIVE") == "ACTIVE")][0].id // .[0].id // empty')"
[[ -n "$ACTIVE_VERSION_ID" ]] || { echo "ERROR: Lockbox has no active version" >&2; exit 4; }
KEYS="$(yc lockbox payload get --id "$LOCKBOX_ID" --version-id "$ACTIVE_VERSION_ID" --format json | jq -r '.entries[]?.key')"
for required in YANDEX_SEARCH_API_KEY GOOGLE_SERVICE_ACCOUNT_JSON; do
  grep -qx "$required" <<<"$KEYS" || {
    echo "ERROR: Lockbox missing key: $required" >&2
    echo "Add the two required entries to secret '$LOCKBOX_ID' and rerun." >&2
    exit 5
  }
done

echo "[2/5] deploy image, revision and hourly timer"
DEPLOY_OUT="$(./deploy/yandex-cloud/deploy-direct.sh)"
printf '%s\n' "$DEPLOY_OUT"

CONTAINER_ID="$(printf '%s\n' "$DEPLOY_OUT" | awk -F= '/^CONTAINER_ID=/{print $2; exit}')"
TRIGGER_ID="$(printf '%s\n' "$DEPLOY_OUT" | awk -F= '/^TRIGGER_ID=/{print $2; exit}')"
[[ -n "$CONTAINER_ID" && -n "$TRIGGER_ID" ]] || { echo "ERROR: deploy did not return container/trigger ids" >&2; exit 6; }

echo "[3/5] verify deployed revision and timer"
yc serverless container get --id "$CONTAINER_ID" --format json | jq '{id,name,status}'
yc serverless trigger get --id "$TRIGGER_ID" --format json | jq '{id,name,status}'

echo "[4/5] production smoke invoke over authenticated HTTPS"
INVOKE_URL="$(yc serverless container get --id "$CONTAINER_ID" --format json | jq -r '.url // empty')"
[[ -n "$INVOKE_URL" ]] || { echo "ERROR: container invocation URL is empty" >&2; exit 7; }
HTTP_CODE="$(curl -sS -o /tmp/supplier-radar-smoke.out -w '%{http_code}' \
  -H "Authorization: Bearer $(yc iam create-token)" \
  -H 'Content-Type: application/json' \
  --data '{"source":"manual-production-smoke"}' \
  "$INVOKE_URL")"
echo "SMOKE_HTTP_CODE=$HTTP_CODE"
if [[ "$HTTP_CODE" -lt 200 || "$HTTP_CODE" -ge 300 ]]; then
  echo "ERROR: smoke invoke failed; body follows" >&2
  cat /tmp/supplier-radar-smoke.out >&2
  exit 8
fi

echo "[5/5] deployment complete; verify Sheet terminal evidence"
echo "YANDEX_AUTOPILOT_DEPLOYED"
echo "CONTAINER_ID=$CONTAINER_ID"
echo "TRIGGER_ID=$TRIGGER_ID"
echo "CRON_UTC=${CRON:-5 * ? * * *}"
echo "SHEET_ID=${SUPPLIER_SHEET_ID:-1oP6pury0HB_M8ajF6--l2PF25m2alB5tjwP7I-YFg0M}"
echo "NEXT_PROOF: confirm START -> SEARCH_BATCH_* -> ENRICH -> DEEP_SCAN -> FINISH in 'Состояние радара', then confirm 3 consecutive timer slots."
