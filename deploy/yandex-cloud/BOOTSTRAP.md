# Supplier Radar — Yandex-native bootstrap

Supplier Radar запускается **только средствами Yandex Cloud**: Serverless Containers + Timer + Lockbox. GitHub Actions в контуре поставщиков не используются.

## Архитектура

```text
Yandex Timer (каждый час)
  -> private Serverless Container supplier-radar
  -> Yandex Search API deferred
  -> direct-site enrichment + geo evidence + deep scan
  -> Google Sheets: Автокандидаты / Состояние радара / Журнал прогонов
```

Основной лист `Поставщики` считается проверенной базой и автоматически сырыми результатами Search API не заполняется.

## 1. Требования

На машине для одноразового bootstrap/deploy нужны:

- авторизованный `yc` CLI;
- `docker`;
- `jq`;
- права в нужном Yandex Cloud folder на IAM, Container Registry, Serverless Containers и Lockbox.

```bash
export YC_FOLDER_ID='<folder-id>'
yc config set folder-id "$YC_FOLDER_ID"
```

## 2. Создать Yandex-native инфраструктуру

Из корня репозитория:

```bash
./deploy/yandex-cloud/bootstrap.sh
```

Скрипт идемпотентно создаёт/находит:

- service account `supplier-radar-runtime`;
- Container Registry `supplier-radar`;
- Lockbox secret `supplier-radar-runtime`;
- минимальные роли runtime account для pull образа, вызова контейнера и чтения конкретного Lockbox secret.

## 3. Lockbox

В **одной ACTIVE версии** секрета `supplier-radar-runtime` должны быть ключи:

- `YANDEX_SEARCH_API_KEY` — ключ Search API;
- `GOOGLE_SERVICE_ACCOUNT_JSON` — JSON сервисного аккаунта Google, которому выдан доступ на запись в таблицу.

Секреты не передаются обычными environment variables и не хранятся в GitHub.

Google Sheet:

```text
1oP6pury0HB_M8ajF6--l2PF25m2alB5tjwP7I-YFg0M
```

Таблица должна быть расшарена с `client_email` из `GOOGLE_SERVICE_ACCOUNT_JSON` с правом редактора.

## 4. Прямой deploy в Yandex Cloud

```bash
export YC_FOLDER_ID='<folder-id>'
export YANDEX_LOCKBOX_SECRET_ID='<secret-id-from-bootstrap>'
./deploy/yandex-cloud/deploy-direct.sh
```

Скрипт:

1. собирает Docker image;
2. пушит его в Yandex Container Registry;
3. создаёт/обновляет private Serverless Container;
4. подставляет Search API и Google credentials из Lockbox;
5. ставит `execution-timeout=3300s` и `concurrency=1`;
6. создаёт Yandex Timer `supplier-radar-hourly` с cron `5 * ? * * *`;
7. печатает `CONTAINER_ID`, `REVISION_ID`, `TRIGGER_ID`.

Cron Yandex Timer задаётся в UTC, но для ежечасного расписания минутная отметка `:05` остаётся `:05` в каждом часовом слоте.

## 5. Что считается успешным первым запуском

В Google Sheet должны появиться:

- `Состояние радара`: строки `START`, `SEARCH_BATCH_*`, `ENRICH`, `DEEP_SCAN`, `FINISH`;
- `Автокандидаты`: новые уникальные кандидаты с `score >= 40`;
- `Журнал прогонов`: итоговая строка `Yandex Cloud / Search API / staging`.

`Поставщики` не должен автоматически пополняться без отдельной гео/B2B проверки.

## 6. Runtime safety

- технический hard limit контейнера: 3300 секунд;
- рабочая цель: до 3000 секунд полезной работы;
- последние ~240 секунд зарезервированы на checkpoint/Sheets/finalize;
- Yandex Search план ограничен бюджетом;
- глубокая проверка продолжается только пока есть сильные кандидаты, без искусственного `sleep` ради 50 минут;
- история берётся из Google Sheet, поэтому перезапуск контейнера не обнуляет память поиска.

## 7. После первого успешного Yandex-прогона

ChatGPT automation `Пушкино — поставщики` можно выключить как поисковый fallback и оставить ChatGPT только для контроля качества/ручной проверки кандидатов. `Радар: автоулучшение 50 мин` — отдельный проект и может оставаться включённым.
