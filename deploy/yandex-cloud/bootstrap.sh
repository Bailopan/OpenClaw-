#!/usr/bin/env bash
set -euo pipefail

: "${YC_FOLDER_ID:?Set YC_FOLDER_ID first}"

RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-supplier-radar-runtime}"
REGISTRY_NAME="${REGISTRY_NAME:-supplier-radar}"
LOCKBOX_SECRET_NAME="${LOCKBOX_SECRET_NAME:-supplier-radar-runtime}"

for c in yc jq; do
  command -v "$c" >/dev/null || { echo "$c CLI is required" >&2; exit 2; }
done

yc config set folder-id "$YC_FOLDER_ID" >/dev/null

yc iam service-account get --name "$RUNTIME_SA_NAME" >/dev/null 2>&1 || \
  yc iam service-account create --name "$RUNTIME_SA_NAME" >/dev/null
RUNTIME_SA_ID="$(yc iam service-account get --name "$RUNTIME_SA_NAME" --format json | jq -r .id)"

ensure_folder_role() {
  local role="$1"
  if ! yc resource-manager folder list-access-bindings "$YC_FOLDER_ID" --format json \
      | jq -e --arg member "serviceAccount:${RUNTIME_SA_ID}" --arg role "$role" \
        '.[] | select(.roleId==$role) | .subjects[]? | select((.type+":"+.id)==$member)' >/dev/null; then
    yc resource-manager folder add-access-binding "$YC_FOLDER_ID" \
      --role "$role" --subject "serviceAccount:$RUNTIME_SA_ID" >/dev/null
  fi
}

ensure_folder_role container-registry.images.puller
ensure_folder_role serverless-containers.containerInvoker

if ! yc container registry get --name "$REGISTRY_NAME" >/dev/null 2>&1; then
  yc container registry create --name "$REGISTRY_NAME" >/dev/null
fi
REGISTRY_ID="$(yc container registry get --name "$REGISTRY_NAME" --format json | jq -r .id)"

if ! yc lockbox secret get --name "$LOCKBOX_SECRET_NAME" >/dev/null 2>&1; then
  yc lockbox secret create --name "$LOCKBOX_SECRET_NAME" --description "Supplier Radar runtime secrets" >/dev/null
fi
LOCKBOX_SECRET_ID="$(yc lockbox secret get --name "$LOCKBOX_SECRET_NAME" --format json | jq -r .id)"

yc lockbox secret add-access-binding \
  --id "$LOCKBOX_SECRET_ID" \
  --role lockbox.payloadViewer \
  --subject "serviceAccount:$RUNTIME_SA_ID" >/dev/null 2>&1 || true

cat <<EOF
YANDEX_BOOTSTRAP_OK
YC_FOLDER_ID=$YC_FOLDER_ID
RUNTIME_SA_ID=$RUNTIME_SA_ID
REGISTRY_ID=$REGISTRY_ID
YANDEX_LOCKBOX_SECRET_ID=$LOCKBOX_SECRET_ID

The Lockbox secret must contain one ACTIVE version with these keys:
- YANDEX_SEARCH_API_KEY
- GOOGLE_SERVICE_ACCOUNT_JSON

Then deploy directly from this repository:
export YC_FOLDER_ID='$YC_FOLDER_ID'
export YANDEX_LOCKBOX_SECRET_ID='$LOCKBOX_SECRET_ID'
./deploy/yandex-cloud/deploy-direct.sh
EOF
