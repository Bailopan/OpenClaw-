# Yandex Cloud deployment

Supplier Radar runs as a **Yandex-native** loop: private Serverless Container + Yandex Timer + Lockbox. GitHub Actions are not used for supplier scheduling or deployment.

## Runtime

The application listens on `$PORT` and accepts timer invocations on `POST /`.

Production defaults in `deploy-direct.sh` / `config/pushkino.json`:

- 1 vCPU
- 1 GB RAM
- container execution timeout: 3300 seconds
- useful-work target: about 3000 seconds when the candidate frontier justifies it
- shutdown/checkpoint reserve before timeout
- private container
- revision concurrency: 1
- Yandex Timer: `5 * ? * * *` (UTC cron)

The runtime never sleeps merely to reach 50 minutes. After Search API discovery it spends remaining useful time on direct-site enrichment, physical-location evidence and deep scans of stronger candidates.

## Secrets

Store runtime secrets in Yandex Lockbox, not GitHub and not normal revision environment values:

- `YANDEX_SEARCH_API_KEY`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

Normal non-secret environment values include:

- `YANDEX_FOLDER_ID`
- `SUPPLIER_SHEET_ID=1oP6pury0HB_M8ajF6--l2PF25m2alB5tjwP7I-YFg0M`
- `SUPPLIER_RADAR_CONFIG=/app/config/pushkino.json`

The runtime service account needs access to the specific Lockbox secret and permission to invoke/pull the Yandex resources used by the container/timer.

## Google Sheets contract

Machine discoveries go to `Автокандидаты`, health/checkpoints to `Состояние радара`, and run summaries to `Журнал прогонов`.

`Поставщики` is the verified base. Raw Search API rows are never automatically promoted there.

Durable discovery history is reconstructed from the verified base plus staging candidates, so a fresh serverless instance does not reset the search memory.

## Search cost guard

Current Pushkino config caps the Search API plan at:

- 50 requests/run;
- 1.60 ₽ estimated Search budget/run;
- 40 ₽ estimated Search budget/day;
- 24 expected hourly runs/day.

The `0.0305 ₽/request` value in config is a planning estimate used by the guard; actual Yandex billing should be treated as the source of truth.

## Deploy

One-time Yandex-side bootstrap:

```bash
export YC_FOLDER_ID='<folder-id>'
./deploy/yandex-cloud/bootstrap.sh
```

After adding `YANDEX_SEARCH_API_KEY` and `GOOGLE_SERVICE_ACCOUNT_JSON` to one ACTIVE Lockbox version:

```bash
export YC_FOLDER_ID='<folder-id>'
export YANDEX_LOCKBOX_SECRET_ID='<secret-id>'
./deploy/yandex-cloud/deploy-direct.sh
```

A successful deploy prints the container, revision and timer IDs. A successful first timer run must then appear in `Состояние радара` with a terminal `FINISH` heartbeat.
