# Пушкино — план запуска автоматического поиска через Yandex Cloud

## Цель

Независимый от ChatGPT Scheduled Tasks контур: Yandex Timer каждый час запускает Supplier Radar, Yandex Search API собирает кандидатов, pipeline проверяет прямые сайты/гео/B2B и пишет staging + health/checkpoints в Google Sheet. GitHub Actions не используются.

## Definition of Done

Система считается реально запущенной только после 3 последовательных автоматических hourly run, каждый из которых имеет в `Состояние радара` собственный Run ID и terminal `FINISH`, а в `Журнал прогонов` — итоговую строку Yandex Cloud/Search API. Наличие Trigger без этих записей не считается успехом.

## P0 — deploy

1. Авторизовать `yc` CLI и выбрать folder (`YC_FOLDER_ID`).
2. Выполнить `./deploy/yandex-cloud/deploy-all.sh`.
3. Убедиться, что Lockbox active version содержит `YANDEX_SEARCH_API_KEY` и `GOOGLE_SERVICE_ACCOUNT_JSON`; значения не логировать.
4. Deploy создаёт/обновляет service account, Container Registry, Serverless Container revision (timeout 3300s, concurrency 1) и Timer `supplier-radar-hourly`.
5. Выполнить authenticated production smoke invoke.
6. Проверить Sheet: START → SEARCH_BATCH_* → ENRICH → DEEP_SCAN → FINISH.

## P1 — доказать hourly automation

1. Не запускать вручную между тремя контрольными слотами.
2. Для каждого слота проверить уникальный Run ID, START, terminal FINISH, число запросов/raw/unique/new candidates и расход Search API.
3. Если slot пропущен — смотреть trigger/container logs и terminal blocker; не подменять его ChatGPT fallback.
4. После 3/3 успешных слотов поставить статус `YANDEX_AUTOPILOT=LIVE`.

## P2 — довести полезную длительность

1. Не держать контейнер искусственным sleep.
2. Пока есть рабочий бюджет и новые направления — ротировать ветки A–R, адресные кластеры и категории.
3. Цель до 3000 секунд активного work budget, последние минуты — finalize/read-back/checkpoint.
4. Если сильные кандидаты кончились раньше — честно закончить раньше; длительность не важнее качества.
5. Раз в run фиксировать фактические START/FINISH/duration.

## P3 — качество и дедуп

- Домен + ИНН + телефон + физический адрес + relation graph.
- Юрадрес/SEO-страница/доставка в Пушкино не являются подтверждением склада.
- Raw Search API никогда напрямую не повышает строку до `Поставщики`.
- `score >= 40` только staging; финальный статус через гео+B2B QA.
- Хранить negative memory, чтобы не перепроверять ZaZa/Delta/ELASGO/чистых логистов без нового сигнала.

## P4 — эксплуатация

- Каждый run: START + изменяющиеся checkpoints + terminal status.
- Дневной лимит Search API и расчётный/фактический расход.
- Watchdog: нет FINISH в ожидаемом слоте → ERROR/MISSED_SLOT.
- Не включать ChatGPT fallback автоматически, пока диагностируется Yandex production, иначе он маскирует проблему.

## Текущий blocker

Код deploy/pipeline уже есть. В активном ChatGPT runtime нет `yc`, Docker и YC credentials и нет Yandex Cloud connector. Поэтому остаётся один внешний bootstrap: выполнить `yc init`/иметь авторизованный профиль в Yandex Cloud и запустить `deploy-all.sh`. После этого источник правды — Sheet health, а не факт создания trigger.
