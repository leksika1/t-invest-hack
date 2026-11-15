# Карточка облигации

Конкурсный проект «AI в инвестиционной торговле» для аналитика долгового рынка, инвестсоветника и андеррайтера. Целевой MVP: ISIN → MOEX ISS и ИНН → автосбор ПРАЙМ/СКРИН/официальный Интерфакс → выбор PDF или загрузка своих → 15 полей с цитатами, источниками и сверкой ISS → проверка человеком и Markdown-экспорт. Приоритет — корпоративные облигации ВДО.

Факты из документов эмитента, не инвестиционная рекомендация. LLM-значение без подтверждённой цитаты скрывается; прямое значение ISS помечается источником MOEX ISS и extracted.

**Реализованы этапы 2–4 и инструментарий этапа 5:** ISS и документы, автосбор/OCR/FTS, асинхронная карточка из 15 типизированных полей, OpenRouter-шлюз с UTC-бюджетом и журналом, гибридный поиск, проверка цитат/ISS, правки, Markdown и защищённый admin API. SPA: подготовка выпуска, выбор/загрузка PDF, прогресс, карточка с цитатами и формами исправлений, история. Светлая/тёмная тема выбирается системными настройками. Без ключа LLM показан баннер: доступны поля ISS, рисковые not_found с причиной «LLM не настроена». POST /api/cards возвращает 202 с card_id/job_id. Оценщик gold/метрик/baseline и JSON/Markdown-отчёты готовы; качество реальной модели на корпусе 30–50 выпусков ещё не подтверждено.

Ссылки SPA: `/#/`, `/#/issue/RU000A10ATW2`, `/#/cards/{id}`, `/#/history?isin=RU000A10ATW2`. Прямая ссылка на карточку восстанавливает результат и polling после перезагрузки. Локальная разработка и тесты фронтенда — [OPERATIONS](docs/OPERATIONS.md).

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
- [EVALUATION](docs/EVALUATION.md) — работающий CLI, gold/разметка, baseline, отчёты и незавершённый реальный эксперимент.
- [DEVELOPMENT](docs/DEVELOPMENT.md) — этапы 2–5 и критерии готовности.
- [OPERATIONS](docs/OPERATIONS.md) — Dokploy Dev/PROD, CI, хуки и эксплуатация.
- [Исходная конкурсная анкета](docs/contest/answers_4-8.md) — исторические ответы команды, уточнённые текущим MVP.

## Переменные окружения

В Dokploy ключ LLM задаётся в **Environment сервиса Backend**. Для платных вызовов также нужны актуальные тарифы и положительный бюджет; стоимость usage.cost приоритетна. Файл .env* в рамках задания не изменяется. Настройки бэкенда реализованы и передаются обоими backend Compose. Ключи модели и ADMIN_TOKEN во фронтенд не передаются.

| Переменная | Где / статус | Значение и назначение |
|---|---|---|
| `POSTGRES_PASSWORD` | Backend, сейчас | Обязателен; латиница/цифры для текущего URL БД |
| `DEV_API_TARGET` | Только локальный Vite dev server | По умолчанию http://localhost:8000; можно задать в shell https://dev.tinvest.leksika1.ru. Не попадает в bundle и не нужен в Compose |
| `DATABASE_URL` | Backend, сейчас | Compose формирует URL БД bonds; для запуска без Docker задать явно |
| `BACKEND_ALIAS` | Backend + Frontend, Dokploy | `tinvest-backend-dev` / `tinvest-backend-prod` |
| `SHARED_NETWORK` | Dokploy, сейчас | Общая сеть, по умолчанию `dokploy-network` |
| `CORS_ORIGINS` | Backend, сейчас | Домены через запятую, если API на другом origin |
| `ISS_BASE_URL`, `ISS_TIMEOUT` | Backend, этап 2 | `https://iss.moex.com/iss`, 10 с |
| `LLM_BASE_URL` | Backend, этап 3 | По умолчанию `https://openrouter.ai/api/v1` |
| `LLM_MODEL` | Backend, этап 3 | По умолчанию `google/gemini-2.5-flash` |
| `LLM_API_KEY` | Backend, сейчас | Ключ OpenRouter; без ключа целевой MVP работает в деградированном режиме |
| `PUBLIC_BASE_URL` | Backend, этап 3 | URL сайта для HTTP-Referer; X-Title: Bond Card MVP |
| `LLM_DAILY_BUDGET_USD` | Backend, этап 3 | 1 USD/UTC-день по умолчанию; 0 блокирует модель. Учитываются расходы и незакрытые резервы |
| `LLM_INPUT_PRICE_PER_1M_USD`, `LLM_OUTPUT_PRICE_PER_1M_USD` | Backend, этап 3 | USD/1 млн токенов для резерва/fallback. Для `google/gemini-2.5-flash` пропущено/пусто → `0.30`/`2.50`; явные значения переопределяют тарифы. Для другой модели нужны оба тарифа, иначе `llm_prices_not_configured`. Проверить актуальность на [openrouter.ai/models](https://openrouter.ai/models) |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `LLM_MAX_OUTPUT_TOKENS` | Backend, этап 3 | 45 с, 2 повтора временной ошибки, 4096 выходных токенов |
| `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` | Backend, этап 3 | Опционально, base/key наследуют LLM; DIM=1024 (1–2000), не настроено/ошибка → FTS |
| `EMBEDDING_INPUT_PRICE_PER_1M_USD` | Backend, этап 3 | Явный тариф эмбеддингов для общего бюджета; /embeddings конкретной модели проверить |
| `EDISCLOSURE_API_URL`, `EDISCLOSURE_API_KEY` | Backend, этап 2 | Без пары official_access_required, с парой adapter_not_implemented; нужен договорной API |
| `COLLECTOR_USER_AGENT` | Backend, этап 2 | `BondCardMVP/0.1 (+https://tinvest.leksika1.ru)` |
| `COLLECTOR_DELAY_SECONDS`, `COLLECTOR_TIMEOUT_SECONDS`, `COLLECTOR_CACHE_SECONDS` | Backend, этап 2 | 1 с (минимум), 25 с, 3600 с |
| `COLLECTOR_MAX_DOCUMENTS`, `COLLECTOR_MAX_PAGES` | Backend, этап 2 | 20 кандидатов/источник, 12 страниц СКРИН |
| `ADMIN_TOKEN` | Backend, этап 3 | Защита admin API через X-Admin-Token; пусто → закрыт (503) |
| `FILES_DIR`, `MAX_UPLOAD_BYTES`, `MAX_UPLOAD_FILES` | Backend, этап 2 | `/data/files` в volume, 31457280 байт/PDF, 5 файлов/запрос |
| `MAX_PDF_PAGES`, `MAX_PAGE_PIXELS`, `OCR_DPI`, `OCR_TIMEOUT_SECONDS`, `DOCUMENT_PARSE_TIMEOUT_SECONDS` | Backend, этап 2 | 1000 страниц, 25 млн пикселей, 150 dpi, 60 с/страницу, 300 с/документ |
| `MAX_ARCHIVE_BYTES`, `MAX_ARCHIVE_UNPACKED_BYTES`, `MAX_ARCHIVE_ENTRIES`, `MAX_ARCHIVE_RATIO` | Backend, этап 2 | 100 МиБ, 150 МиБ, 100 элементов, коэффициент 100; ZIP, RAR не поддерживается |
| `WORKER_ENABLED`, `JOB_POLL_SECONDS`, `JOB_LEASE_SECONDS`, `JOB_MAX_ATTEMPTS` | Backend, этап 2 | true, 1 с, 180 с, 3 попытки |
| `QUOTE_FUZZY_THRESHOLD`, `EXTRACTION_MIN_CONFIDENCE` | Backend, этап 3 | 0.95 и 0.8; пороги пока не калиброваны реальным экспериментом |
| `RETRIEVAL_TOP_K`, `RETRIEVAL_MAX_TOKENS` | Backend, этап 3 | 8 фрагментов, консервативный предел контекста 10000 UTF-8 байт |
| `RATE_LIMIT_WINDOW_SECONDS`, `RATE_LIMIT_CARDS`, `RATE_LIMIT_UPLOADS`, `RATE_LIMIT_COLLECT` | Backend, этап 3 | PostgreSQL-лимит: 60 с, 5 карточек, 10 загрузок, 5 автосборов на адрес клиента |
| `E2E_PORT`, `E2E_DB_PORT` | Локально, сейчас | HTTP/БД: overlay 18080/55433; scripts/ci.sh 28080/55434 |
| `TEST_DATABASE_URL`, `TEST_DB_PORT` | Тесты | Отдельная БД *_test; scripts/ci.sh поднимает bonds_test на 55435; без URL db-тесты пропускаются |

Шлюз использует usage.cost, иначе токены/тарифы конфига. Реальные модельные вызовы в разработке этапа 3 не выполнялись. Демо автосбора: RU000A10ATW2 («Балтийский лизинг», ПРАЙМ) и RU000A10CS75 («Аэрофлот», СКРИН). Живое скачивание проверено 15.11.2026; ссылки и ограничения — [DATA](docs/DATA.md).
