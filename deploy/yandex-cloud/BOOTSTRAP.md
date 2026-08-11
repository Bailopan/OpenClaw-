# One-time Yandex Cloud bootstrap

После этого bootstrap деплой выполняется GitHub Actions по OIDC без долгоживущего Yandex ключа в GitHub.

## 0. Требование

Нужен один раз авторизованный `yc` CLI с правами создавать IAM/service accounts, role bindings, Container Registry, Lockbox и Serverless resources.

```bash
export YC_FOLDER_ID='<folder-id>'
yc config set folder-id "$YC_FOLDER_ID"
```

## 1. Service accounts

### GitHub deploy account

```bash
yc iam service-account create --name supplier-radar-github
DEPLOY_SA_ID="$(yc iam service-account get --name supplier-radar-github --format json | jq -r .id)"

for ROLE in \
  container-registry.images.pusher \
  serverless-containers.editor \
  iam.serviceAccounts.user
do
  yc resource-manager folder add-access-binding "$YC_FOLDER_ID" \
    --role "$ROLE" \
    --subject "serviceAccount:$DEPLOY_SA_ID"
done
```

### Container runtime + timer account

```bash
yc iam service-account create --name supplier-radar-runtime
RUNTIME_SA_ID="$(yc iam service-account get --name supplier-radar-runtime --format json | jq -r .id)"

for ROLE in \
  container-registry.images.puller \
  serverless-containers.containerInvoker
do
  yc resource-manager folder add-access-binding "$YC_FOLDER_ID" \
    --role "$ROLE" \
    --subject "serviceAccount:$RUNTIME_SA_ID"
done
```

`lockbox.payloadViewer` лучше выдать runtime account на конкретные supplier-radar secrets, а не на весь folder.

## 2. Container Registry

```bash
yc container registry create --name supplier-radar
YC_REGISTRY_ID="$(yc container registry get --name supplier-radar --format json | jq -r .id)"
```

## 3. GitHub Workload Identity Federation

Issuer / audience / subject привязаны только к текущему GitHub owner/repository/main.

```bash
yc iam workload-identity oidc federation create \
  --name supplier-radar-github \
  --issuer 'https://token.actions.githubusercontent.com' \
  --audiences 'https://github.com/Bailopan' \
  --jwks-url 'https://token.actions.githubusercontent.com/.well-known/jwks'

FEDERATION_ID="$(yc iam workload-identity oidc federation get --name supplier-radar-github --format json | jq -r .id)"

yc iam workload-identity federated-credential create \
  --service-account-id "$DEPLOY_SA_ID" \
  --federation-id "$FEDERATION_ID" \
  --external-subject-id 'repo:Bailopan/OpenClaw-:ref:refs/heads/main'
```

If the GitHub repository is renamed later, recreate/update the federated credential subject to the new repository name.

## 4. Lockbox

Create one secret, e.g. `supplier-radar-runtime`, with these keys:

- `YANDEX_SEARCH_API_KEY` — required Search API key.
- `GOOGLE_SERVICE_ACCOUNT_JSON` — optional; required only for direct Google Sheets append.
- `SUPPLIER_SEEDS_JSON` — optional private list of seed companies, for example `["Company A","Company B"]`.

Give `supplier-radar-runtime` service account `lockbox.payloadViewer` on this secret.

The Search API key should belong to a service account that is allowed to use Search API and be scoped to Search API execution.

## 5. GitHub repository variables

In GitHub Actions repository variables set:

```text
YC_SA_ID=<DEPLOY_SA_ID>
YC_RUNTIME_SA_ID=<RUNTIME_SA_ID>
YC_FOLDER_ID=<YC_FOLDER_ID>
YC_REGISTRY_ID=<YC_REGISTRY_ID>
SUPPLIER_SHEET_ID=1oP6pury0HB_M8ajF6--l2PF25m2alB5tjwP7I-YFg0M
```

And `YC_REVISION_SECRETS` as multiline text, substituting the real Lockbox secret ID:

```text
YANDEX_SEARCH_API_KEY=<secret-id>/latest/YANDEX_SEARCH_API_KEY
GOOGLE_SERVICE_ACCOUNT_JSON=<secret-id>/latest/GOOGLE_SERVICE_ACCOUNT_JSON
SUPPLIER_SEEDS_JSON=<secret-id>/latest/SUPPLIER_SEEDS_JSON
```

Nonexistent optional keys should be removed from `YC_REVISION_SECRETS` until they exist.

## 6. First deploy

Run GitHub Action `Deploy Supplier Radar to Yandex Cloud` manually, or push to `main` after all variables are set.

The action creates/updates a private Serverless Container named `supplier-radar` and prints `Container ID` / `Revision ID` to the GitHub Actions summary.

## 7. Hourly timer

After the first deploy:

```bash
CONTAINER_ID='<container-id-from-deploy>'

yc serverless trigger create timer \
  --name supplier-radar-hourly \
  --cron-expression '0 * ? * * *' \
  --invoke-container-id "$CONTAINER_ID" \
  --invoke-container-service-account-id "$RUNTIME_SA_ID"
```

Yandex timer cron is UTC. The query planner itself rotates search branches by UTC hourly slot, so every invocation does not repeat exactly the same 100 queries.

## 8. Budget guard

Current config uses deferred Search API and caps the search plan to the lower of:

- 100 requests/run;
- 10 ₽/run;
- `100 ₽ / expected_runs_per_day`.

At 24 runs/day and planning price 0.0305 ₽/request, the daily guard reduces the effective maximum to ~4.17 ₽/run; a full 100-query run costs about 3.05 ₽ of Search API calls, so it stays below the configured daily cap before any future LLM stage.
