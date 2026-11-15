# Карточка облигации

Конкурсный проект «AI в инвестиционной торговле» для аналитика долгового рынка, инвестсоветника и андеррайтера. Целевой MVP: ISIN → MOEX ISS и ИНН → автосбор ПРАЙМ/СКРИН/официальный Интерфакс → выбор PDF или загрузка своих → 15 полей с цитатами, источниками и сверкой ISS → проверка человеком и Markdown-экспорт. Приоритет — корпоративные облигации ВДО.

Факты из документов эмитента, не инвестиционная рекомендация. LLM-значение без подтверждённой цитаты скрывается; прямое значение ISS помечается источником MOEX ISS и extracted.

**Реализован этап 2:** Alembic/БД, ISS с эмитентом и графиками, автосбор ПРАЙМ/СКРИН, загрузка PDF и ZIP коллекторов, OCR, FTS, jobs и API документов/выбора. e-disclosure — заглушка официального API и ссылка на ручной поиск. Интерфейс пока прежний; LLM/сборка карточки — этап 3. Существующий POST карточки остаётся синхронным: 16 совместимых полей, not_found с причиной extraction_pending_stage3.

## Быстрый старт

Нужны Docker и Compose v2. Из корня в Bash/Git Bash:

```bash
# Секреты задайте в окружении shell; .env* в рамках задания не редактируются.
export POSTGRES_PASSWORD=localdev
# При необходимости: LLM_API_KEY и явные LLM_BASE_URL/LLM_MODEL из таблицы ниже.
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build --wait
curl http://127.0.0.1:18080/api/health
```

Приложение: `http://127.0.0.1:18080`, Swagger: `/api/docs`. Запуск без Docker, деплой, остановка и проверки — в [OPERATIONS](docs/OPERATIONS.md).

## Документация

- [ARCHITECTURE](docs/ARCHITECTURE.md) — компоненты, поток, БД, статусы и безопасность.
- [CARD_FIELDS](docs/CARD_FIELDS.md) — 15 полей, значения, подсказки и сверка.
- [DATA](docs/DATA.md) — источники, ограничения, кэш и непроверенные предположения.
- [API](docs/API.md) — текущие и будущие маршруты, автосбор и выбор документов.
- [EVALUATION](docs/EVALUATION.md) — эксперимент, эталон, baseline и пороги качества.
- [DEVELOPMENT](docs/DEVELOPMENT.md) — этапы 2–5 и критерии готовности.
- [OPERATIONS](docs/OPERATIONS.md) — Dokploy Dev/PROD, CI, хуки и эксплуатация.
- [Исходная конкурсная анкета](docs/contest/answers_4-8.md) — исторические ответы команды, уточнённые текущим MVP.

## Переменные окружения

В Dokploy ключ LLM задаётся в **Environment сервиса Backend**. «План» — настройка этапа 3, ещё не поддерживаемая кодом/Compose. Настройки этапа 2 ниже передаются в оба backend-определения Compose; подробности — OPERATIONS.

| Переменная | Где / статус | Значение и назначение |
|---|---|---|
| `POSTGRES_PASSWORD` | Backend, сейчас | Обязателен; латиница/цифры для текущего URL БД |
| `DATABASE_URL` | Backend, сейчас | Compose формирует URL БД bonds; для запуска без Docker задать явно |
| `BACKEND_ALIAS` | Backend + Frontend, Dokploy | `tinvest-backend-dev` / `tinvest-backend-prod` |
| `SHARED_NETWORK` | Dokploy, сейчас | Общая сеть, по умолчанию `dokploy-network` |
| `CORS_ORIGINS` | Backend, сейчас | Домены через запятую, если API на другом origin |
| `ISS_BASE_URL`, `ISS_TIMEOUT` | Backend, этап 2 | `https://iss.moex.com/iss`, 10 с |
| `LLM_BASE_URL` | Backend, принимается сейчас | Целевой default `https://openrouter.ai/api/v1`; в каркасе указать явно |
| `LLM_MODEL` | Backend, принимается сейчас | Целевой default `google/gemini-2.5-flash`; в каркасе указать явно |
| `LLM_API_KEY` | Backend, сейчас | Ключ OpenRouter; без ключа целевой MVP работает в деградированном режиме |
| `PUBLIC_BASE_URL` | Backend, план | URL сайта для HTTP-Referer; X-Title фиксирован: Bond Card MVP |
| `LLM_DAILY_BUDGET_USD` | Backend, план | Положительный дневной бюджет, UTC |
| `LLM_INPUT_PRICE_PER_1M_USD`, `LLM_OUTPUT_PRICE_PER_1M_USD` | Backend, план | Резерв и fallback при отсутствии usage.cost; тарифы проверить |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` | Backend, план | Таймаут и ограниченные повторы 429/5xx |
| `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` | Backend, этап 2 | Интерфейс; DIM=1024 (1–2000). Платные вызовы выключены до шлюза этапа 3, используется FTS |
| `EMBEDDING_INPUT_PRICE_PER_1M_USD` | Backend, план | Тариф для бюджета и fallback стоимости эмбеддингов |
| `EDISCLOSURE_API_URL`, `EDISCLOSURE_API_KEY` | Backend, этап 2 | Без пары official_access_required, с парой adapter_not_implemented; нужен договорной API |
| `COLLECTOR_USER_AGENT` | Backend, этап 2 | `BondCardMVP/0.1 (+https://tinvest.leksika1.ru)` |
| `COLLECTOR_DELAY_SECONDS`, `COLLECTOR_TIMEOUT_SECONDS`, `COLLECTOR_CACHE_SECONDS` | Backend, этап 2 | 1 с (минимум), 25 с, 3600 с |
| `COLLECTOR_MAX_DOCUMENTS`, `COLLECTOR_MAX_PAGES` | Backend, этап 2 | 20 кандидатов/источник, 12 страниц СКРИН |
| `ADMIN_TOKEN` | Backend, план | Защита admin API через X-Admin-Token; пусто → закрыт |
| `FILES_DIR`, `MAX_UPLOAD_BYTES`, `MAX_UPLOAD_FILES` | Backend, этап 2 | `/data/files` в volume, 31457280 байт/PDF, 5 файлов/запрос |
| `MAX_PDF_PAGES`, `MAX_PAGE_PIXELS`, `OCR_DPI`, `OCR_TIMEOUT_SECONDS`, `DOCUMENT_PARSE_TIMEOUT_SECONDS` | Backend, этап 2 | 1000 страниц, 25 млн пикселей, 150 dpi, 60 с/страницу, 300 с/документ |
| `MAX_ARCHIVE_BYTES`, `MAX_ARCHIVE_UNPACKED_BYTES`, `MAX_ARCHIVE_ENTRIES`, `MAX_ARCHIVE_RATIO` | Backend, этап 2 | 100 МиБ, 150 МиБ, 100 элементов, коэффициент 100; ZIP, RAR не поддерживается |
| `WORKER_ENABLED`, `JOB_POLL_SECONDS`, `JOB_LEASE_SECONDS`, `JOB_MAX_ATTEMPTS` | Backend, этап 2 | true, 1 с, 180 с, 3 попытки |
| `QUOTE_FUZZY_THRESHOLD`, `EXTRACTION_MIN_CONFIDENCE` | Backend, план | Зафиксировать на dev-выборке перед оценкой |
| `E2E_PORT`, `E2E_DB_PORT` | Локально, сейчас | HTTP/БД: overlay 18080/55433; scripts/ci.sh 28080/55434 |
| `TEST_DATABASE_URL`, `TEST_DB_PORT` | Тесты | Отдельная БД *_test; scripts/ci.sh поднимает bonds_test на 55435; без URL db-тесты пропускаются |

Стоимость и заголовки OpenRouter — план этапа 3. Демо автосбора: RU000A10ATW2 («Балтийский лизинг», ПРАЙМ) и RU000A10CS75 («Аэрофлот», СКРИН). Живое скачивание проверено 15.11.2026; ссылки и ограничения — [DATA](docs/DATA.md).
