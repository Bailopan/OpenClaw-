# Пушкино Supplier Radar — production checklist

Цель: один раз развернуть Yandex-native контур и дальше искать поставщиков автоматически, независимо от ChatGPT Scheduled Tasks и без GitHub Actions.

## Единственный внешний bootstrap

На машине, где пользователь уже авторизован в Yandex Cloud:

```bash
yc init
export YC_FOLDER_ID='<folder-id>'
./deploy/yandex-cloud/deploy-all.sh
```

`deploy-all.sh` сначала проверяет реальную авторизацию `yc`, затем идемпотентно создаёт инфраструктуру, проверяет наличие только ИМЁН обязательных ключей в Lockbox (значения не печатает), собирает/пушит контейнер, создаёт revision + hourly Timer и делает ручной production smoke invoke.

Lockbox `supplier-radar-runtime` должен иметь активную версию с двумя ключами:

- `YANDEX_SEARCH_API_KEY`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

## Как доказать, что автопоиск реально работает

Успех — не наличие trigger и не `last_run_time`. Источник правды — Google Sheet `1oP6pury0HB_M8ajF6--l2PF25m2alB5tjwP7I-YFg0M`.

Для каждого run в `Состояние радара` должны быть terminal evidence:

```text
START -> SEARCH_BATCH_* -> ENRICH -> DEEP_SCAN -> FINISH
```

Потом должны пройти 3 последовательных timer-slot без ручного запуска. Только после этого считать `YANDEX_AUTOPILOT=LIVE`.

## Расписание

Timer: `5 * ? * * *` — каждый час в `:05` UTC. Для почасового cron минутная отметка остаётся `:05` в каждом часовом слоте.

## Long-run режим

Revision timeout: `3300s` (55 минут). Цель приложения — до 3000 секунд полезной работы и резерв на checkpoint/finalize. Yandex Serverless Containers поддерживает обработку до 1 часа; timeout >10 минут относится к long-lived containers. Не считать 50 минут успешными без фактических timestamps/checkpoints.

## Стоимость и защита

- Search API ограничен конфигом `config/pushkino.json`.
- Сырые кандидаты идут только в `Автокандидаты`.
- `Поставщики` не пополняется автоматически без гео/B2B QA.
- `concurrency=1` снижает риск параллельной гонки внутри revision.
- Любой run должен завершаться terminal checkpoint либо честным blocker/error.

## Если deploy-all остановился

Ошибка до `[2/5]` — проблема локального YC auth/Lockbox.
Ошибка на `[2/5]` — build/deploy/permissions.
Ошибка на `[4/5]` — container invocation/runtime.
Trigger есть, но нет новых строк в Sheet — runtime/credentials/app write defect; не считать систему работающей.
