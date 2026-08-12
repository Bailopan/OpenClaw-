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

---

# P0 — добиться первого настоящего Yandex FINISH

## Шаг 1. Получить/подтвердить Yandex Cloud runtime-доступ

Нужен один авторизованный Yandex Cloud контекст, из которого можно создать ресурсы:

- `yc` CLI авторизован;
- известен `YC_FOLDER_ID`;
- есть права на IAM, Container Registry, Serverless Containers и Lockbox;
- локально доступны `docker` и `jq`.

Проверка:

```bash
yc config list
yc resource-manager folder get "$YC_FOLDER_ID"
docker --version
jq --version
```

Если этого нет, Search API key сам по себе недостаточен для создания Container/Timer.

**Acceptance:** команда `yc resource-manager folder get "$YC_FOLDER_ID"` проходит без ошибки.

## Шаг 2. Создать Yandex-native инфраструктуру

Из корня `Bailopan/OpenClaw-`:

```bash
export YC_FOLDER_ID='<folder-id>'
./deploy/yandex-cloud/bootstrap.sh
```

Скрипт должен создать/найти:

- service account `supplier-radar-runtime`;
- Container Registry `supplier-radar`;
- Lockbox secret `supplier-radar-runtime`;
- роли runtime account для pull образа, вызова контейнера и чтения Lockbox.

Сохранить выведенные:

```text
RUNTIME_SA_ID
REGISTRY_ID
YANDEX_LOCKBOX_SECRET_ID
```

**Acceptance:** `YANDEX_BOOTSTRAP_OK`.

## Шаг 3. Положить секреты в Yandex Lockbox

В одной ACTIVE версии секрета `supplier-radar-runtime` должны быть:

```text
YANDEX_SEARCH_API_KEY
GOOGLE_SERVICE_ACCOUNT_JSON
```

Google service account из `GOOGLE_SERVICE_ACCOUNT_JSON` должен иметь Editor к таблице:

```text
1oP6pury0HB_M8ajF6--l2PF25m2alB5tjwP7I-YFg0M
```

Секреты не переносить в GitHub Actions, обычные repo files или логи.

**Acceptance:** у Lockbox есть ACTIVE version с обоими ключами; Google service account реально может append/read-back в таблицу.

## Шаг 4. Задеплоить Supplier Radar напрямую в Yandex Cloud

```bash
export YC_FOLDER_ID='<folder-id>'
export YANDEX_LOCKBOX_SECRET_ID='<secret-id>'
./deploy/yandex-cloud/deploy-direct.sh
```

Скрипт уже должен:

1. собрать Docker image;
2. push в Yandex Container Registry;
3. создать/обновить private Serverless Container `supplier-radar`;
4. внедрить Search API + Google credentials через Lockbox;
5. поставить `execution-timeout=3300s`;
6. поставить `concurrency=1`;
7. создать Timer `supplier-radar-hourly`;
8. расписание `5 * ? * * *` — каждый час на `:05`;
9. включить 1 retry.

**Acceptance:** вывод содержит `YANDEX_DEPLOY_OK`, `CONTAINER_ID`, `REVISION_ID`, `TRIGGER_ID`.

## Шаг 5. Не ждать следующего часа — сделать ручной production invoke

Сразу после deploy вызвать тот же контейнер вручную тем же runtime route, чтобы проверить production-конфигурацию до Timer.

Проверяем:

- контейнер стартует;
- видит Search API key;
- видит Google credentials;
- читает `config/pushkino.json`;
- пишет START в `Состояние радара`;
- выполняет Search API batch;
- пишет checkpoint;
- пишет кандидатов в `Автокандидаты`;
- завершает FINISH.

**Acceptance:** в `Состояние радара` появилась первая полноценная цепочка START -> SEARCH_BATCH -> ENRICH -> DEEP_SCAN/FINALIZE -> FINISH.

---

# P1 — доказать, что почасовой автозапуск реально работает

## Шаг 6. Проверить Timer

Yandex Timer должен вызывать контейнер каждый час. Для почасового расписания используем `:05`, чтобы запуск было легко отличать от других задач.

Проверить минимум 3 последовательных слота:

```text
HH:05
HH+1:05
HH+2:05
```

Для каждого Run ID должны быть:

- START;
- меняющийся checkpoint;
- Search API activity;
- FINISH или честный ERROR/PARTIAL;
- фактические start/finish;
- raw results;
- unique domains;
- new candidates;
- расход ₽.

**Acceptance:** 3/3 последовательных Yandex Timer run имеют terminal record и не требуют ChatGPT для запуска.

## Шаг 7. Тест 50 минут полезной работы

Yandex Serverless Containers допускает request до 1 часа; в проекте используем:

```text
hard timeout = 3300 s (55 мин)
target active work = 3000 s (50 мин)
shutdown reserve = 240 s
```

50 минут — цель полезной работы, а не sleep. Pipeline должен продолжать:

1. новые поисковые формулировки;
2. enrichment официальных сайтов;
3. geo evidence;
4. B2B evidence;
5. deep scan сильных кандидатов;
6. address/reverse-search;
7. checkpoint после батчей.

Если frontier закончился раньше, run честно заканчивается раньше; нельзя рисовать `50 мин` искусственно.

**Acceptance:** хотя бы один реальный run держится около 50 минут за счёт полезной работы и оставляет несколько промежуточных checkpoint'ов.

---

# P1 — надёжность и восстановление

## Шаг 8. Durable checkpoints

Каждый этап пишет в `Состояние радара`:

```text
Run ID
slot
start
updated
status
stage
queries
raw results
unique domains
new candidates
Search API cost
checkpoint
error/blocker
```

Локальный `/tmp` считается только ускорителем. Источник восстановления — Google Sheet.

При следующем запуске radar должен:

- видеть предыдущий незавершённый Run ID;
- не считать его успешным;
- продолжать с durable history;
- не дублировать уже обработанные домены/ИНН/телефоны.

## Шаг 9. Watchdog

Считать runtime сломанным, если:

- нет нового Yandex START после ожидаемого слота + разумный grace period;
- два последовательных часа нет FINISH;
- Search API постоянно ERROR;
- Sheets write/read-back не проходит.

В `Состояние радара` хранить явный `ERROR`/`BLOCKER`, а не молчание.

ChatGPT не должен быть обязательной частью watchdog. Он может раз в день читать состояние и сообщать проблему, но сам hourly поиск делает Yandex.

---

# P1 — бюджет

## Шаг 10. Жёсткий cost governor

Текущие ограничения проекта:

```text
max Search API requests/run = 50
расчётная цена request = 0.0305 ₽
max Search API cost/run = 1.60 ₽
max Search API cost/day = 40 ₽
expected runs/day = 24
```

Нужно добавить/проверить именно **persisted daily ledger**: расход за день считается по уже записанным Yandex run в `Состояние радара`, а не только как `daily_budget / 24`.

Перед новым batch:

```text
if spent_today + batch_cost > daily_cap:
    stop Search API
    продолжить бесплатный enrichment уже найденных кандидатов
```

**Acceptance:** дневной бюджет нельзя пробить повторными/retry run.

---

# P2 — качество поставщиков

## Шаг 11. Promotion gate

`Автокандидаты` — машинный staging.

Автоматически разрешено:

- новый domain;
- contacts;
- INN;
- price/catalog signals;
- geo evidence;
- B2B evidence;
- score;
- source URLs.

Автоматически запрещено:

- ставить `Подтверждён` в `Поставщики` только из-за слова «Пушкино»;
- считать юрадрес фактическим складом;
- считать SEO delivery page складом;
- переносить retail/logistics-only компании.

В `Поставщики` компания попадает только после geo+B2B QA.

## Шаг 12. Negative memory

Добавить persistent reject registry:

```text
entity/domain
reason
source
rejected_at
recheck_after
source_fingerprint
```

Примеры:

- склад Реутов, не Пушкино;
- только доставка по Пушкино;
- pure logistics;
- retail only;
- ликвидирован;
- юрадрес без physical evidence.

Пока TTL/source не изменился — не тратить Search API/deep scan повторно.

## Шаг 13. Address graph

Каждый подтверждённый физический адрес должен порождать reverse-search:

```text
address -> tenants -> domains -> legal entities -> brands -> phones -> warehouses
```

Приоритетные кластеры текущей базы:

- PNK Пушкино-2;
- МОЛКОМ / Костомаровская;
- Кудринское шоссе;
- Заводская;
- Ярославское шоссе;
- Правдинский / Мамонтовка / Софрино.

---

# P2 — эксплуатация

## Шаг 14. Production dashboard в Google Sheet

Минимальные KPI за сутки:

```text
runs expected / finished / failed
Search API requests
Search API ₽
raw results
unique domains
new staged candidates
geo_verified
B2B_verified
rejected
promoted
median run duration
last successful FINISH
```

Красные состояния:

- 2 часа без FINISH;
- 0 Search API при наличии бюджета и frontier;
- >40 ₽/day;
- staging растёт, QA не разгребается;
- много повторных rejected domains.

---

# Порядок запуска — без расползания

Делаем строго так:

1. `yc` auth + `YC_FOLDER_ID`.
2. `bootstrap.sh`.
3. Lockbox keys.
4. Google Sheet Editor для service account.
5. `deploy-direct.sh`.
6. ручной invoke production container.
7. проверить первую цепочку START -> FINISH.
8. проверить 3 последовательных Timer slots.
9. проверить один длинный полезный run около 50 минут.
10. после этого улучшать cost ledger / negative memory / address graph / QA.

Не начинаем новые архитектурные переделки до первого настоящего Yandex FINISH.

---

# Definition of Done

Yandex-поиск поставщиков Пушкино считается **реально запущенным автоматически**, только если одновременно выполняется всё:

- Timer enabled;
- Container revision active;
- 3 последовательных hourly slots получили START;
- 3 последовательных hourly slots получили terminal FINISH/PARTIAL/ERROR record;
- хотя бы один успешный run реально вызвал Yandex Search API;
- `Автокандидаты` пополняются без дублей;
- `Состояние радара` содержит checkpoints;
- `Журнал прогонов` содержит итог;
- `Поставщики` не загрязняется сырыми кандидатами;
- daily cost cap соблюдается;
- ChatGPT Scheduled Task не требуется для запуска поиска.

До выполнения этого Definition of Done нельзя говорить пользователю «Яндекс-прогоны пашут».
