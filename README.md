## AI Assistant Runtime

Сервис состоит из двух приложений:
- `agent` (`ai_service.main`) — LangGraph-агент, reminders, RAG, `call_me -> amoCRM`.
- `telegram_profile` (`telegram_service.app`) — доставка сообщений в Telegram.

### Docker Compose

```bash
docker compose up -d
```

Поднимаются сервисы:
- `agent`
- `telegram_profile`
- `postgres`
- `redis`
- `db-init`

Порты хоста по умолчанию:
- `agent`: `18000 -> 8000` (настраивается через `AGENT_PORT`)
- `telegram_profile`: `18001 -> 8001` (через `TELEGRAM_PROFILE_PORT`)
- `postgres` и `redis` наружу не публикуются (без конфликтов с `5432/6379`)

### Основные API `agent`

- `GET /health`
- `POST /answer`
- `POST /stt_description`
- `POST /photo_description`
- `POST /delete_thread`

### `.env` обязательный минимум

- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`
- `API_ID`, `API_HASH`, `TELEGRAM_SESSION`
- `REDIS_URL`
- `FIXED_TOUCHES_ENABLED=1`
- `REMINDER_TZ=Asia/Tashkent`
- `FIXED_TOUCH_SAFE_START_HOUR=9`
- `FIXED_TOUCH_SAFE_END_HOUR=22`
- `AMO_SUBDOMAIN`, `AMO_ACCESS_TOKEN`
- `AMO_PIPELINE_ID=9873894`
- `AMO_STATUS_ID=78511606`
- `AMO_RESPONSIBLE_USER_ID`
- `AMO_PRICE_FALLBACK_SUM=125000`
- `AMO_SOURCE_DEFAULT=ТГ`
- `PAID_GROUP_LINK`

### Fixed touches (обычные касания)

Используется `ai_service/config/fixed_touches.json`.
Текущий сценарий:
- `touch_1` через 2 часа
- `touch_2` через 6 часов

Безопасное окно отправки для fixed:
- `09:00–22:00` по `Asia/Tashkent`

### Умное касание

`schedule_reminder` и `Delreminder` работают отдельно от fixed-цепочки.

### Оплатный сценарий (`call_me`)

При подтвержденной оплате агент вызывает `call_me`.

`call_me`:
1. Проверяет обязательные поля: `name`, `phone`, `payment_check`.
2. Делает upsert в amoCRM по телефону.
3. Создает/обновляет сделку в `pipeline/status` из env.
4. Возвращает статусы:
- `CALL_ME_OK`
- `CALL_ME_NEED_FIELDS`
- `CALL_ME_CRM_FAIL`

Если в `CALL_ME_OK` пришел `group_link`, агент сразу отправляет ссылку клиенту.

### RAG

RAG использует папку `data/docs`.
Если папка пуста, tool `search_docs_knowledge` возвращает статус о пустой базе знаний.

Текущая структура документов:
- `data/docs/01_trening_1_siyanie_kozhi.md`
- `data/docs/02_oteki_3_rekomendacii.md`
- `data/docs/03_ekspertnost_umida.md`
- `data/docs/04_lichnost_umida.md`
- `data/docs/05_faq_offer_vozrazheniya.md`

### Фото-паки (`send_photo_pack`)

- `file_id=1` — рисовая маска для лица (рецепт для выравнивания тона/пигментации).
- `file_id=2` — дополнительный продуктовый фото-пак.
- `file_id=3` — дополнительный продуктовый фото-пак.

### Как агент использует знания

Для продуктовых и экспертных вопросов агент работает в режиме `RAG-first`:
1. Сначала вызывает `search_docs_knowledge`.
2. Затем дает клиенту короткий ответ в формате `~` по найденным фактам.
