# Yandex Cloud deployment

Целевой контур: Serverless Container + Timer trigger + Lockbox.

Секреты не коммитить.

Нужны как минимум:
- `YANDEX_SEARCH_API_KEY`
- `YANDEX_FOLDER_ID`
- доступ на запись в целевую Google Sheet на этапе подключения экспорта

Контейнер собирается из корневого `Dockerfile` и запускает `python -m supplier_radar.main`.
