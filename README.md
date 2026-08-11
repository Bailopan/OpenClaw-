# supplier-radar

Отдельный проект поиска поставщиков для Пушкино и северо-востока Московской области.

Архитектура v1:

Yandex Cloud cron -> Python -> Yandex Search API -> фильтрация/дедуп -> Google Sheets.

OpenAI API не используется. YandexGPT в v1 не требуется.
