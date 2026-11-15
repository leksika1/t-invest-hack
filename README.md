# Карточка облигации

Конкурсный проект «AI в инвестиционной торговле» для аналитика долгового рынка, инвестсоветника и андеррайтера. Целевой MVP: ISIN → MOEX ISS и ИНН → автосбор ПРАЙМ/СКРИН/официальный Интерфакс → выбор PDF или загрузка своих → 15 полей с цитатами, источниками и сверкой ISS → проверка человеком и Markdown-экспорт. Приоритет — корпоративные облигации ВДО.

Факты из документов эмитента, не инвестиционная рекомендация. LLM-значение без подтверждённой цитаты скрывается; прямое значение ISS помечается источником MOEX ISS и extracted.

**Сейчас реализован каркас:** FastAPI, Postgres, React-форма, health, ISS description и POST карточки-заглушки (`not_implemented`). Автосбор, PDF/OCR, jobs, извлечение и шлюз с бюджетом ещё предстоит реализовать. Этапы 1/1b меняют документацию.

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

В Dokploy ключ LLM задаётся в **Environment сервиса Backend**. «План» означает, что настройка ещё не поддерживается кодом/Compose. Новые настройки при реализации добавить в pydantic-settings и оба backend-определения Compose; подробности — OPERATIONS.

| Переменная | Где / статус | Значение и назначение |
|---|---|---|
| `POSTGRES_PASSWORD` | Backend, сейчас | Обязателен; латиница/цифры для текущего URL БД |
| `DATABASE_URL` | Backend, сейчас | Compose формирует URL БД bonds; для запуска без Docker задать явно |
| `BACKEND_ALIAS` | Backend + Frontend, Dokploy | `tinvest-backend-dev` / `tinvest-backend-prod` |
| `SHARED_NETWORK` | Dokploy, сейчас | Общая сеть, по умолчанию `dokploy-network` |
| `CORS_ORIGINS` | Backend, сейчас | Домены через запятую, если API на другом origin |
| `LLM_BASE_URL` | Backend, принимается сейчас | Целевой default `https://openrouter.ai/api/v1`; в каркасе указать явно |
| `LLM_MODEL` | Backend, принимается сейчас | Целевой default `google/gemini-2.5-flash`; в каркасе указать явно |
| `LLM_API_KEY` | Backend, сейчас | Ключ OpenRouter; без ключа целевой MVP работает в деградированном режиме |
| `PUBLIC_BASE_URL` | Backend, план | URL сайта для HTTP-Referer; X-Title фиксирован: Bond Card MVP |
| `LLM_DAILY_BUDGET_USD` | Backend, план | Положительный дневной бюджет, UTC |
| `LLM_INPUT_PRICE_PER_1M_USD`, `LLM_OUTPUT_PRICE_PER_1M_USD` | Backend, план | Резерв и fallback при отсутствии usage.cost; тарифы проверить |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` | Backend, план | Таймаут и ограниченные повторы 429/5xx |
| `EMBEDDING_BASE_URL` | Backend, план | По умолчанию LLM_BASE_URL; поддержку /embeddings OpenRouter проверить |
| `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` | Backend, план | Опциональные эмбеддинги; не настроено/ошибка → только FTS |
| `EMBEDDING_INPUT_PRICE_PER_1M_USD` | Backend, план | Тариф для бюджета и fallback стоимости эмбеддингов |
| `EDISCLOSURE_API_URL`, `EDISCLOSURE_API_KEY` | Backend, план | Только официальный договорной API; без пары выключен, пока заглушка |
| `COLLECTOR_USER_AGENT` | Backend, план | Идентификатор проекта с реальным контактом (контакт уточнить) |
| `COLLECTOR_DELAY_SECONDS`, `COLLECTOR_TIMEOUT_SECONDS` | Backend, план | Вежливый опрос источников, значения определить на этапе 2 |
| `ADMIN_TOKEN` | Backend, план | Защита admin API через X-Admin-Token; пусто → закрыт |
| `FILES_DIR`, `MAX_UPLOAD_BYTES` | Backend, план | `/data/files`, 31457280 байт на PDF; volume ещё не подключён |
| `QUOTE_FUZZY_THRESHOLD`, `EXTRACTION_MIN_CONFIDENCE` | Backend, план | Зафиксировать на dev-выборке перед оценкой |
| `E2E_PORT`, `E2E_DB_PORT` | Локально, сейчас | HTTP/БД: overlay 18080/55433; scripts/ci.sh 28080/55434 |

Стоимость и заголовки OpenRouter, ограничения коллекторов и конфигурация сред описаны в OPERATIONS. Значения по умолчанию и новые настройки здесь — целевые решения для следующих этапов, а не изменения приложения.
