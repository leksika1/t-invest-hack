# Карточка облигации

Конкурсный проект «AI в инвестиционной торговле».

По ISIN биржевой корпоративной облигации (приоритет — ВДО) система собирает эмиссионные документы
(решение о выпуске, проспект, сущфакты), LLM извлекает около 15 полей карточки выпуска с цитатой и ссылкой
на страницу, а «жёсткие» поля сверяются с MOEX ISS
(`https://iss.moex.com/iss/securities/{secid}.json`, `/iss/securities/{secid}/bondization.json`).
Результат — карточка с флагами по каждому полю: «сверено с ISS», «расхождение», «не найдено», «требует проверки».
Поле без цитаты не показывается.

Пользователь — аналитик долгового рынка или инвестсоветник. Карточка содержит факты из документов
и не является инвестиционной рекомендацией.

Ответы команды на анкету (архитектура, данные): [docs/answers_4-8.md](docs/answers_4-8.md).

## Состояние

Сейчас это каркас: бэкенд, фронтенд и Postgres поднимаются одной командой, `POST /api/cards` возвращает
карточку-заглушку (у всех полей `status: not_implemented`). Сбор документов, LLM-извлечение и сверка с ISS
пока не реализованы.

## Структура

```
backend/                     FastAPI (Python 3.12)
  app/main.py                приложение и /api/health; все маршруты под /api
  app/config.py              настройки из переменных окружения (pydantic-settings)
  app/db.py                  SQLAlchemy 2 + psycopg, проверка соединения
  app/cards/schema.py        поля карточки (Pydantic) — будущая JSON-схема для LLM
  app/cards/router.py        POST /api/cards
  app/iss/                   клиент MOEX ISS и GET /api/iss/{secid}
  app/llm/client.py          обёртка над OpenAI-совместимым API
  tests/                     unit-тесты pytest
  tests/e2e/                 e2e-тесты по HTTP против поднятого стека
  docker-compose.yml         сервис «Backend» в Dokploy: backend + Postgres (pgvector)
frontend/                    Vite + React + TypeScript
  Dockerfile                 сборка статики → nginx (шаблон конфига с BACKEND_UPSTREAM)
  docker-compose.yml         сервис «Frontend» в Dokploy: только nginx
docker-compose.yml           весь стек для локального запуска и CI
docker-compose.e2e.yml       публикация портов для e2e и локальной работы
.github/workflows/ci.yml     CI/CD
docs/                        документы команды
```

## API

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/health` | `{status, db, llm, version}`; `db` — проверка соединения с Postgres, `llm` — `configured` / `not_configured` |
| POST | `/api/cards` | тело `{"isin": "RU000A10AXF7"}`; проверяет формат ISIN (`RU` + 9 букв/цифр + цифра, без контрольной суммы), иначе 422; возвращает карточку-заглушку |
| GET | `/api/iss/{secid}` | базовые параметры выпуска из MOEX ISS; ISS недоступен — 502, выпуск не найден — 404 |

Swagger UI: `/api/docs`.

## Переменные окружения

| Переменная | Где | Назначение |
|---|---|---|
| `POSTGRES_PASSWORD` | Backend, обязательно | пароль Postgres; только латиница и цифры — входит в URL подключения |
| `BACKEND_ALIAS` | Backend и Frontend (Dokploy), обязательно | имя backend в общей сети: `tinvest-backend-dev` / `tinvest-backend-prod` |
| `LLM_BASE_URL` | Backend | адрес OpenAI-совместимого API; пусто — официальный OpenAI |
| `LLM_API_KEY` | Backend | ключ API; пусто — LLM не используется |
| `LLM_MODEL` | Backend | название модели |
| `CORS_ORIGINS` | Backend | домены через запятую, если фронтенд ходит в API с другого домена; обычно не нужно |
| `SHARED_NETWORK` | Backend и Frontend | общая сеть, по умолчанию `dokploy-network`; менять только для локальной проверки |
| `E2E_PORT`, `E2E_DB_PORT` | локально | порты `docker-compose.e2e.yml` на 127.0.0.1 (по умолчанию 18080 и 55433) |

Пользователь и база Postgres — `bonds`. Порт Postgres наружу не публикуется, данные — в томе `pgdata`.

## Локальный запуск в Docker

```bash
cp .env.example .env      # задайте POSTGRES_PASSWORD и при необходимости LLM_*
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build --wait
```

Приложение — `http://127.0.0.1:18080` (порт меняется через `E2E_PORT`). Корневой `docker-compose.yml`
сам порты не публикует, общая сеть Dokploy для него не нужна.

```bash
curl http://127.0.0.1:18080/api/health
cd backend && E2E_BASE_URL=http://127.0.0.1:18080 pytest -q tests/e2e
```

Остановить: `docker compose -f docker-compose.yml -f docker-compose.e2e.yml down` (с `-v` удалятся и данные БД).

## Деплой в Dokploy

Организация **T-Invest**, два проекта:

| Проект | Ветка | Домен | `BACKEND_ALIAS` | GitHub Environment |
|---|---|---|---|---|
| Dev | `dev` | `dev.tinvest.leksika1.ru` | `tinvest-backend-dev` | `dev` |
| PROD | `main` | `tinvest.leksika1.ru` | `tinvest-backend-prod` | `prod` |

В каждом проекте два сервиса типа Compose из этого репозитория (провайдер Git по SSH, ветка проекта):

| Сервис | Compose Path | Domains | Environment |
|---|---|---|---|
| Backend | `./backend/docker-compose.yml` | хост проекта, Path `/api`, порт 8000, сервис `backend` | `POSTGRES_PASSWORD`, `BACKEND_ALIAS`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, при необходимости `CORS_ORIGINS` |
| Frontend | `./frontend/docker-compose.yml` | хост проекта, Path `/`, порт 80, сервис `frontend` | `BACKEND_ALIAS` (то же значение, что у Backend) |

- Backend отвечает по путям `/api/...` как есть, поэтому strip prefix в домене не включать.
- Фронтенд проксирует `/api` в backend через сеть `dokploy-network` по имени `BACKEND_ALIAS`.
  Nginx стартует, даже если backend ещё не поднят: до его запуска `/api` отвечает 502.
- Postgres доступен только backend (внутренняя сеть `data`), данные — в томе `pgdata` каждого проекта.
- Автодеплой по push в Dokploy выключить: деплой запускает CI после зелёной сборки.
  Webhook URL каждого сервиса (Dokploy → сервис → Deployments) положить в секреты GitHub:
  Settings → Environments → `dev` / `prod` → `DOKPLOY_WEBHOOK_BACKEND`, `DOKPLOY_WEBHOOK_FRONTEND`.

## CI/CD

`.github/workflows/ci.yml` запускается на push и pull request в `dev` и `main`:

1. **backend tests** — `pip-audit -r requirements.txt` и `pytest -q`. Новые тесты в `backend/tests/` подхватываются
   сами; e2e без `E2E_BASE_URL` пропускаются.
2. **frontend checks** — `npm ci`, `npm audit --audit-level=high` (с повторами, если сервис аудита недоступен),
   `npm run lint`, `npm run typecheck`, `npm run build`. Место для `npm test` отмечено в workflow.
3. **build** — проверка `backend/` и `frontend/docker-compose.yml` (`config -q`), сборка образов,
   подъём стека `docker-compose.yml` + `docker-compose.e2e.yml`, `pytest tests/e2e` через nginx,
   логи при падении, `down -v` всегда.
4. **deploy** — только на push: вебхуки Dokploy для frontend и backend из окружения `prod` (ветка `main`)
   или `dev` (ветка `dev`); шаг падает, если Dokploy ответил не 200.

Работа идёт в `dev` → проверка на `dev.tinvest.leksika1.ru` → merge в `main` → прод.

## Разработка без Docker

Бэкенд (нужен Postgres; адрес задаётся `DATABASE_URL`, например
`postgresql+psycopg://bonds:пароль@127.0.0.1:55433/bonds` — база из `docker-compose.e2e.yml`):

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt      # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest
.venv/bin/uvicorn app.main:app --reload
```

Фронтенд (dev-сервер проксирует `/api` на `http://localhost:8000`):

```bash
cd frontend
npm install
npm run dev          # npm run lint, npm run typecheck — те же проверки, что в CI
```

## Как развивать

- Новое поле карточки — атрибут в `IssVerifiableFields` или `RiskTextFields` в `backend/app/cards/schema.py`.
- Вызовы модели — только через `LLMClient` из `backend/app/llm/client.py`.
- Запросы к MOEX ISS — через `backend/app/iss/client.py`.
- Новая переменная окружения backend — добавить в `environment` обоих compose-файлов (корневого и `backend/`),
  иначе заданная в Dokploy переменная до контейнера не дойдёт.
