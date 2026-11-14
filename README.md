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
backend/                 FastAPI (Python 3.12)
  app/main.py            приложение и /api/health
  app/config.py          настройки из переменных окружения (pydantic-settings)
  app/db.py              SQLAlchemy 2 + psycopg, проверка соединения
  app/cards/schema.py    поля карточки (Pydantic) — будущая JSON-схема для LLM
  app/cards/router.py    POST /api/cards
  app/iss/               клиент MOEX ISS и GET /api/iss/{secid}
  app/llm/client.py      обёртка над OpenAI-совместимым API
  tests/                 pytest
frontend/                Vite + React + TypeScript, в Docker собирается в статику и отдаётся nginx
  nginx.conf             статика + проксирование /api/ на бэкенд
docs/                    документы команды
docker-compose.yml       db (pgvector/pgvector:pg16) + backend + frontend
.env.example             пример переменных окружения
```

## API

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/health` | `{status, db, llm, version}`; `db` — проверка соединения с Postgres, `llm` — `configured` / `not_configured` |
| POST | `/api/cards` | тело `{"isin": "RU000A10AXF7"}`; проверяет формат ISIN (`RU` + 9 букв/цифр + цифра, без контрольной суммы), иначе 422; возвращает карточку-заглушку |
| GET | `/api/iss/{secid}` | базовые параметры выпуска из MOEX ISS; если ISS недоступен — 502 |

Swagger UI бэкенда: `/docs` (в Docker-сборке наружу не проксируется, доступен при локальном запуске бэкенда).

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `APP_PORT` | `80` | внешний порт приложения (nginx) |
| `POSTGRES_DB` | `bonds` | имя базы |
| `POSTGRES_USER` | `bonds` | пользователь Postgres |
| `POSTGRES_PASSWORD` | — (обязательно) | пароль Postgres; используйте буквы и цифры, без `@ : / ? #` — пароль подставляется в URL подключения |
| `LLM_BASE_URL` | пусто | адрес OpenAI-совместимого API; пусто — официальный OpenAI |
| `LLM_API_KEY` | пусто | ключ API; пусто — LLM не используется |
| `LLM_MODEL` | пусто | название модели |

Порт Postgres наружу не публикуется, данные хранятся в именованном томе `pgdata`.

## Запуск в Docker (локально или на сервере)

Нужны Docker и Docker Compose v2.

```bash
cp .env.example .env      # затем задайте POSTGRES_PASSWORD и при необходимости LLM_*
docker compose up -d --build
```

Откройте `http://localhost` (или `http://localhost:<APP_PORT>`). Проверка бэкенда:

```bash
curl http://localhost/api/health
curl -X POST http://localhost/api/cards -H "Content-Type: application/json" -d '{"isin":"RU000A10AXF7"}'
```

Остановить: `docker compose down` (данные БД сохраняются; `docker compose down -v` удалит и их).

## Деплой на сервер

```bash
git clone <url-репозитория> bond-card
cd bond-card
cp .env.example .env
nano .env                 # пароль БД, ключ LLM, APP_PORT
docker compose up -d --build
```

Обновление:

```bash
git pull && docker compose up -d --build
```

Логи: `docker compose logs -f backend`, статус: `docker compose ps`.

## Разработка без Docker

Бэкенд (нужен доступный Postgres; адрес задаётся `DATABASE_URL`, например
`postgresql+psycopg://bonds:пароль@localhost:5432/bonds`):

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
npm run dev
```

## Как развивать

- Новое поле карточки — атрибут в `IssVerifiableFields` или `RiskTextFields` в `backend/app/cards/schema.py`.
- Вызовы модели — только через `LLMClient` из `backend/app/llm/client.py`.
- Запросы к MOEX ISS — через `backend/app/iss/client.py`.
