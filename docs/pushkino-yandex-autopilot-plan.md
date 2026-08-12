# Пушкино — план запуска автоматического поиска поставщиков на Yandex Cloud

Статус: **план до production, без GitHub Actions**.

Цель: каждый час Yandex Cloud сам запускает Supplier Radar для Пушкино, выполняет полезный поиск и проверку поставщиков, сохраняет checkpoint'ы и кандидатов в Google Sheet. ChatGPT не нужен как почасовой scheduler.

## Конечная архитектура

```text
Yandex Timer (каждый час)
  -> private Serverless Container `supplier-radar`
  -> Yandex Search API
  -> direct-site enrichment
  -> geo/B2B evidence
  -> deep scan сильных кандидатов
  -> Google Sheets
       - Состояние радара
       - Автокандидаты
       - Журнал прогонов
  -> ручная/ChatGPT QA только сильных кандидатов
  -> Поставщики
```

Главное правило: Search API никогда автоматически не пишет сырые результаты в `Поставщики`. Только в staging `Автокандидаты`.

## P0 — первый настоящий Yandex FINISH

1. Авторизованный `yc` CLI, известный `YC_FOLDER_ID`, Docker и jq.
2. Запуск `./deploy/yandex-cloud/deploy-all.sh`.
3. Lockbox `supplier-radar-runtime`: `YANDEX_SEARCH_API_KEY` + `GOOGLE_SERVICE_ACCOUNT_JSON`.
4. Google service account имеет Editor к таблице `1oP6pury0HB_M8ajF6--l2PF25m2alB5tjwP7I-YFg0M`.
5. Deploy создаёт/обновляет service account, Registry, private Container revision, timeout 3300s, concurrency 1 и Timer `supplier-radar-hourly`.
6. После deploy — authenticated production smoke invoke.
7. Acceptance: `Состояние радара` содержит START -> SEARCH_BATCH_* -> ENRICH -> DEEP_SCAN -> FINISH.

## P1 — доказать почасовую автоматизацию

Проверить 3 последовательных timer slot без ручного запуска. Для каждого Run ID должны быть START, меняющиеся checkpoints, Search API activity, FINISH/честный ERROR, фактическая длительность, raw/unique/new candidates и расход ₽. Только после 3/3 ставить `YANDEX_AUTOPILOT=LIVE`.

## P2 — полезная длительность

Yandex Serverless Containers допускает обработку запроса до 1 часа; timeout >10 минут относится к long-lived container. В проекте hard timeout 3300s, target active work до 3000s, финальный резерв на checkpoint. Никаких искусственных sleep: если frontier исчерпан, run честно заканчивается раньше.

## P3 — качество

Дедуп: домен + ИНН + телефон + физический адрес + relation graph. Юрадрес/SEO/доставка в Пушкино не подтверждают склад. Raw Search API не попадает напрямую в `Поставщики`; `score >= 40` — только staging до geo+B2B QA. Хранить negative memory для уже опровергнутых связок.

## P4 — эксплуатация

Каждый run пишет START/checkpoints/terminal, дневной расход Search API и blocker. Watchdog: отсутствие ожидаемого FINISH — ERROR/MISSED_SLOT. ChatGPT fallback не должен маскировать проблемы Yandex production.

## Текущий blocker

Код deploy/pipeline подготовлен. В runtime этого чата нет `yc`, Docker, YC credentials и нет Yandex Cloud connector. Единственный внешний bootstrap — авторизовать Yandex Cloud и запустить `deploy-all.sh`; дальше источник правды — Sheet health.
