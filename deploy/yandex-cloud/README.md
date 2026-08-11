# Yandex Cloud deployment

Supplier Radar is designed for **Serverless Containers + Timer**.

## Runtime

The application listens on `$PORT`, as required by Serverless Containers, and accepts timer invocations on `POST /`.

Recommended revision settings:

- 1 vCPU
- 512 MB–1 GB RAM
- execution timeout: 60 minutes
- private container
- service account attached to revision
- timer service account with `serverless-containers.containerInvoker`

Hourly timer cron (UTC):

```text
0 * ? * * *
```

## Secrets / environment

Store secrets in Lockbox and expose them to the revision; do not commit them:

- `YANDEX_SEARCH_API_KEY`
- `YANDEX_FOLDER_ID`
- optional `GOOGLE_SERVICE_ACCOUNT_JSON`
- optional `SUPPLIER_SHEET_ID`
- optional `SUPPLIER_SEEDS_JSON` (private seed companies; keep in Lockbox, not public GitHub)

If Google credentials are absent, the run still succeeds and emits structured JSON to Cloud Logging; Sheets writing is skipped.

## Search cost guard

The default Pushkino config uses deferred Search API with a planning rate of `0.0305 ₽/request` and at most 100 search requests per run, i.e. about `3.05 ₽` of Search API cost per full run before any future paid LLM stage.
