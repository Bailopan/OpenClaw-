# Supplier Radar через Yandex Cloud Functions — путь без Docker

Это предпочтительный быстрый deploy-path, если нужен именно автоматический поиск поставщиков Пушкино каждый час и не хочется поднимать Docker/Container Registry.

Yandex Cloud Functions поддерживает Python 3.12, зависимости из `requirements.txt`, Lockbox secrets, Timer trigger и execution timeout до одного часа. В проекте используется 3300 секунд.

## Запуск

Нужны только авторизованный `yc` CLI и `jq`:

```bash
export YC_FOLDER_ID='<folder-id>'
./deploy/yandex-cloud/deploy-function.sh
```

Скрипт:

1. проверяет доступ к folder;
2. создаёт/находит `supplier-radar-runtime`;
3. проверяет Lockbox `supplier-radar-runtime` и ключи `YANDEX_SEARCH_API_KEY` + `GOOGLE_SERVICE_ACCOUNT_JSON`;
4. создаёт/обновляет Cloud Function `supplier-radar-pushkino`;
5. загружает только нужный Python-код и минимальные runtime dependencies;
6. ставит timeout 3300s и concurrency 1;
7. пересоздаёт Timer `supplier-radar-hourly` на `5 * ? * * *`;
8. делает реальный smoke-run, если `SKIP_SMOKE=1` не задан.

Если Docker недоступен, этот вариант проще Serverless Containers и сохраняет ту же схему Sheet/checkpoint/Search API.

Успех доказывается не созданием функции, а строками в Google Sheet: `START -> SEARCH_BATCH_* -> ENRICH -> DEEP_SCAN -> FINISH`, после чего нужны 3 последовательных timer-run без ручного запуска.
