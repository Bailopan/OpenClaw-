#!/usr/bin/env bash
set -euo pipefail

: "${YC_FOLDER_ID:?Set YC_FOLDER_ID}"
: "${YANDEX_SEARCH_API_KEY:?Set YANDEX_SEARCH_API_KEY}"

CONTAINER_NAME="${CONTAINER_NAME:-supplier-radar}"
TRIGGER_NAME="${TRIGGER_NAME:-supplier-radar-hourly}"
REGISTRY_NAME="${REGISTRY_NAME:-supplier-radar}"
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-supplier-radar-runtime}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
MEMORY="${MEMORY:-1GB}"
CORES="${CORES:-1}"
EXECUTION_TIMEOUT="${EXECUTION_TIMEOUT:-3300s}"
CRON="${CRON:-7 * ? * * *}"

for c in yc docker jq; do command -v "$c" >/dev/null || { echo "$c is required" >&2; exit 2; }; done

yc config set folder-id "$YC_FOLDER_ID" >/dev/null

ensure_sa() {
  if ! yc iam service-account get --name "$RUNTIME_SA_NAME" >/dev/null 2>&1; then
    yc iam service-account create --name "$RUNTIME_SA_NAME" >/dev/null
  fi
  yc iam service-account get --name "$RUNTIME_SA_NAME" --format json | jq -r .id
}

ensure_role() {
  local sa="$1" role="$2"
  if ! yc resource-manager folder list-access-bindings "$YC_FOLDER_ID" --format json | jq -e --arg m "serviceAccount:${sa}" --arg r "$role" '.[]|select(.roleId==$r)|.subjects[]?|select((.type+":"+.id)==$m)' >/dev/null; then
    yc resource-manager folder add-access-binding "$YC_FOLDER_ID" --role "$role" --subject "serviceAccount:$sa" >/dev/null
  fi
}

RUNTIME_SA_ID="$(ensure_sa)"
ensure_role "$RUNTIME_SA_ID" container-registry.images.puller
ensure_role "$RUNTIME_SA_ID" serverless-containers.containerInvoker

if ! yc container registry get --name "$REGISTRY_NAME" >/dev/null 2>&1; then
  yc container registry create --name "$REGISTRY_NAME" >/dev/null
fi
REGISTRY_ID="$(yc container registry get --name "$REGISTRY_NAME" --format json | jq -r .id)"
IMAGE="cr.yandex/${REGISTRY_ID}/supplier-radar:${IMAGE_TAG}"

yc container registry configure-docker >/dev/null
docker build -t "$IMAGE" .
docker push "$IMAGE"

if ! yc serverless container get --name "$CONTAINER_NAME" >/dev/null 2>&1; then
  yc serverless container create --name "$CONTAINER_NAME" --description "Hourly Pushkino supplier radar" >/dev/null
fi
CONTAINER_ID="$(yc serverless container get --name "$CONTAINER_NAME" --format json | jq -r .id)"

yc serverless container revision deploy \
  --container-id "$CONTAINER_ID" \
  --image "$IMAGE" \
  --service-account-id "$RUNTIME_SA_ID" \
  --memory "$MEMORY" \
  --cores "$CORES" \
  --execution-timeout "$EXECUTION_TIMEOUT" \
  --concurrency 1 \
  --environment "YANDEX_SEARCH_API_KEY=${YANDEX_SEARCH_API_KEY},YANDEX_FOLDER_ID=${YC_FOLDER_ID},SUPPLIER_RADAR_CONFIG=/app/config/pushkino.json,SUPPLIER_RADAR_HISTORY=/tmp/supplier-history.json" >/dev/null

if yc serverless trigger get --name "$TRIGGER_NAME" >/dev/null 2>&1; then
  yc serverless trigger delete --name "$TRIGGER_NAME" >/dev/null
fi

yc serverless trigger create timer \
  --name "$TRIGGER_NAME" \
  --cron-expression "$CRON" \
  --payload '{"source":"supplier-radar-hourly"}' \
  --invoke-container-id "$CONTAINER_ID" \
  --invoke-container-path / \
  --invoke-container-service-account-id "$RUNTIME_SA_ID" \
  --retry-attempts 1 \
  --retry-interval 30s >/dev/null

REVISION_ID="$(yc serverless container revision list --container-id "$CONTAINER_ID" --format json | jq -r '.[0].id')"
TRIGGER_ID="$(yc serverless trigger get --name "$TRIGGER_NAME" --format json | jq -r .id)"

cat <<EOF
YANDEX_DEPLOY_OK
CONTAINER_ID=$CONTAINER_ID
REVISION_ID=$REVISION_ID
TRIGGER_ID=$TRIGGER_ID
CRON=$CRON
EXECUTION_TIMEOUT=$EXECUTION_TIMEOUT
IMAGE=$IMAGE
EOF
