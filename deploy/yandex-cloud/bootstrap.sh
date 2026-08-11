#!/usr/bin/env bash
set -euo pipefail

: "${YC_FOLDER_ID:?Set YC_FOLDER_ID first}"

REPO_OWNER="${GITHUB_REPOSITORY_OWNER:-Bailopan}"
REPO_NAME="${GITHUB_REPOSITORY_NAME:-OpenClaw-}"
DEPLOY_SA_NAME="supplier-radar-github"
RUNTIME_SA_NAME="supplier-radar-runtime"
REGISTRY_NAME="supplier-radar"
FEDERATION_NAME="supplier-radar-github"

command -v yc >/dev/null || { echo "yc CLI is required" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 2; }

yc config set folder-id "$YC_FOLDER_ID" >/dev/null

ensure_sa() {
  local name="$1"
  if ! yc iam service-account get --name "$name" >/dev/null 2>&1; then
    yc iam service-account create --name "$name" >/dev/null
  fi
  yc iam service-account get --name "$name" --format json | jq -r .id
}

ensure_folder_role() {
  local sa_id="$1" role="$2"
  if ! yc resource-manager folder list-access-bindings "$YC_FOLDER_ID" --format json \
      | jq -e --arg member "serviceAccount:${sa_id}" --arg role "$role" \
        '.[] | select(.roleId==$role) | .subjects[]? | select((.type+":"+.id)==$member)' >/dev/null; then
    yc resource-manager folder add-access-binding "$YC_FOLDER_ID" \
      --role "$role" --subject "serviceAccount:$sa_id" >/dev/null
  fi
}

DEPLOY_SA_ID="$(ensure_sa "$DEPLOY_SA_NAME")"
RUNTIME_SA_ID="$(ensure_sa "$RUNTIME_SA_NAME")"

for role in \
  container-registry.images.pusher \
  serverless-containers.editor \
  iam.serviceAccounts.user
do
  ensure_folder_role "$DEPLOY_SA_ID" "$role"
done

for role in \
  container-registry.images.puller \
  serverless-containers.containerInvoker
do
  ensure_folder_role "$RUNTIME_SA_ID" "$role"
done

if ! yc container registry get --name "$REGISTRY_NAME" >/dev/null 2>&1; then
  yc container registry create --name "$REGISTRY_NAME" >/dev/null
fi
YC_REGISTRY_ID="$(yc container registry get --name "$REGISTRY_NAME" --format json | jq -r .id)"

if ! yc iam workload-identity oidc federation get --name "$FEDERATION_NAME" >/dev/null 2>&1; then
  yc iam workload-identity oidc federation create \
    --name "$FEDERATION_NAME" \
    --issuer 'https://token.actions.githubusercontent.com' \
    --audiences "https://github.com/${REPO_OWNER}" \
    --jwks-url 'https://token.actions.githubusercontent.com/.well-known/jwks' >/dev/null
fi
FEDERATION_ID="$(yc iam workload-identity oidc federation get --name "$FEDERATION_NAME" --format json | jq -r .id)"
SUBJECT="repo:${REPO_OWNER}/${REPO_NAME}:ref:refs/heads/main"

existing_credential="$(yc iam workload-identity federated-credential list \
  --service-account-id "$DEPLOY_SA_ID" --format json \
  | jq -r --arg fed "$FEDERATION_ID" --arg sub "$SUBJECT" \
    '.[] | select(.federationId==$fed and .externalSubjectId==$sub) | .id' | head -1)"
if [[ -z "$existing_credential" ]]; then
  yc iam workload-identity federated-credential create \
    --service-account-id "$DEPLOY_SA_ID" \
    --federation-id "$FEDERATION_ID" \
    --external-subject-id "$SUBJECT" >/dev/null
fi

cat <<EOF
BOOTSTRAP_OK

Set these GitHub Actions repository variables:
YC_SA_ID=$DEPLOY_SA_ID
YC_RUNTIME_SA_ID=$RUNTIME_SA_ID
YC_FOLDER_ID=$YC_FOLDER_ID
YC_REGISTRY_ID=$YC_REGISTRY_ID
SUPPLIER_SHEET_ID=1oP6pury0HB_M8ajF6--l2PF25m2alB5tjwP7I-YFg0M

OIDC subject:
$SUBJECT

Next:
1) Create Lockbox secret supplier-radar-runtime.
2) Add YANDEX_SEARCH_API_KEY (required).
3) Optionally add GOOGLE_SERVICE_ACCOUNT_JSON and SUPPLIER_SEEDS_JSON.
4) Grant this runtime SA lockbox.payloadViewer on that secret:
   $RUNTIME_SA_ID
5) Set YC_REVISION_SECRETS in GitHub variables.
6) Run Deploy Supplier Radar to Yandex Cloud.
EOF
