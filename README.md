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
scripts/ci.sh                локальный CI (полный или --fast / --no-e2e)
scripts/setup-hooks.sh       установка pre-push хука
.githooks/pre-push           проверки перед push в dev/main
.github/workflows/ci.yml     резервный CI/CD при включённых GitHub Actions
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

## Автодеплой

GitHub Actions для приватного репозитория выключены на аккаунте. Для деплоя после push настройте
прямые вебхуки репозитория в Dokploy. Повторите для каждого из **четырёх** сервисов:

| Проект | Сервис | Ветка в настройках Dokploy |
|---|---|---|
| Dev | Backend | `dev` |
| Dev | Frontend | `dev` |
| PROD | Backend | `main` |
| PROD | Frontend | `main` |

1. В Dokploy: сервис → **Deployments → Webhook URL**, скопируйте URL этого сервиса.
2. В GitHub: репозиторий → **Settings → Webhooks → Add webhook**.
3. **Payload URL** = скопированный URL; **Content type** = `application/json`;
   событие **Just the push event**. Сохраните вебхук.

Dokploy сам сравнивает ветку события с настройкой сервиса: Dev деплоит `dev`, PROD — `main`.
Без вебхуков используйте кнопку **Deploy** в Dokploy. Секреты GitHub Environments для прямых
вебхуков не нужны. Push запускает деплой сразу; проверки до него обеспечивает локальный хук.

В `.github/workflows/ci.yml` сохранён прежний пайплайн, включая `deploy`: он заработает при включении
Actions. Перед включением отключите прямые вебхуки либо джобу `deploy`, чтобы не запускать деплой дважды.

## Проверки перед вливанием

Нужны Bash (на Windows — Git Bash), Python 3.12+, Node.js/npm (в workflow — Node.js 22)
и запущенный Docker с Compose v2. Первый запуск устанавливает зависимости и собирает образы,
поэтому ему нужен доступ к реестрам пакетов и образов.

Один раз после clone подключите хук (на Windows выполняйте команды в Git Bash):

```bash
bash scripts/setup-hooks.sh
```

Это устанавливает `git config core.hooksPath .githooks` и права исполнения скриптов.
Ручной запуск из корня репозитория:

```bash
bash scripts/ci.sh             # полный CI
bash scripts/ci.sh --fast      # без аудитов и e2e
bash scripts/ci.sh --no-e2e    # с аудитами, без e2e
```

Этапы выполняются по порядку; в конце выводится сводка со статусами и временем:

1. **Backend:** создание `backend/.venv` при отсутствии (Windows `Scripts/`, Linux/macOS `bin/`),
   тихий `pip install -r requirements-dev.txt` при изменении хеша requirements,
   `pip-audit -r requirements.txt`, `pytest -q`. E2E здесь пропускаются без `E2E_BASE_URL`.
2. **Frontend:** `npm ci` при отсутствии `node_modules` или изменении хеша `package-lock.json`,
   `npm audit --audit-level=high`, lint, typecheck и build. Недоступность сервиса npm audit:
   три попытки, затем предупреждение; найденные уязвимости проваливают CI.
3. **Compose:** доступность Docker и `config -q` для обоих файлов Dokploy с заглушками
   `POSTGRES_PASSWORD` / `BACKEND_ALIAS`. Этот этап остаётся и в `--fast` / `--no-e2e`.
4. **E2E:** отдельный Compose-проект `tinvest-ci`, сборка, `up -d --wait`, `pytest -q tests/e2e`,
   логи при падении, удаление стека и томов через `down -v` с обработчиком выхода (`trap`).
   Другие Compose-проекты не затрагиваются.

Для CI порты по умолчанию — `28080` (HTTP) и `55434` (БД), отдельно от обычного локального запуска.
Перед стартом проверяется доступность портов. Переопределение:

```bash
E2E_PORT=28081 E2E_DB_PORT=55435 bash scripts/ci.sh
```

`--fast` пропускает аудиты и e2e, но при необходимости всё ещё устанавливает зависимости.
Метки зависимостей хранятся внутри игнорируемых `.venv` / `node_modules`; удалить метку — повторить установку.
Не меняйте зависимости вручную в этих каталогах в обход requirements / lock-файла.

Хук читает все ref из stdin Git. Если push обновляет `refs/heads/dev` или `refs/heads/main`,
он запускает полный `scripts/ci.sh` один раз; ненулевой код отменяет push.
Для остальных веток и удаления ветки проверка не запускается. Проверяется текущая рабочая копия:
перед push переключитесь на отправляемую ветку и закоммитьте изменения.
Сознательный обход: `git push --no-verify`.

Слияние PR через веб-интерфейс GitHub **не запускает локальный хук**. Вливайте локально
(`git merge` + `git push`) или запускайте `bash scripts/ci.sh` на ветке PR перед merge;
после изменения ветки повторяйте проверку.

Этапы `.github/workflows/ci.yml` и `scripts/ci.sh` должны оставаться согласованными:
при изменении проверок обновляйте оба файла.

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
