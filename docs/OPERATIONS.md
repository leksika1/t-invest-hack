# Эксплуатация: Dokploy, CI и хуки

Состояние сверено статически с репозиторием на этапе 1b (15.11.2026). Живой Dokploy и Docker здесь не проверялись. Ниже текущий каркас; будущие изменения отмечены отдельно. Переменные — в [README](../README.md), план — [DEVELOPMENT](DEVELOPMENT.md).

Остановить локальный стек без удаления данных:

```bash
docker compose -f docker-compose.yml -f docker-compose.e2e.yml down
```

`down -v` удаляет тома БД; после появления files-volume удалит и документы. Не применять к средам с нужными данными.

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

## CI/CD и автодеплой

`.github/workflows/ci.yml` выполняется на **self-hosted раннере на сервере Dokploy** (GitHub-hosted раннеры
для этого приватного репозитория недоступны):

- раннер `tinvest-server`, метки `self-hosted, tinvest`; каталог `/home/gha-runner/actions-runner`,
  пользователь `gha-runner` (в группе `docker`), systemd-сервис
  `actions.runner.leksika1-t-invest-hack.tinvest-server`;
- статус: GitHub → Settings → Actions → Runners или `sudo systemctl status actions.runner.leksika1-t-invest-hack.tinvest-server`;
- джобы идут по очереди (один раннер); `build` поднимает e2e-стек как отдельный compose-проект и
  в конце делает `down -v`, рабочие сервисы Dokploy не затрагиваются. Сервер небольшой (2 vCPU, 4 ГБ) —
  тяжёлые проверки добавлять с оглядкой.

Деплой — джоба `deploy` после зелёного `build`, только на push: ветка `dev` → окружение `dev`, `main` → `prod`.
В каждом окружении (GitHub → Settings → Environments) секреты `DOKPLOY_WEBHOOK_BACKEND` и
`DOKPLOY_WEBHOOK_FRONTEND` = Webhook URL соответствующего сервиса (Dokploy → сервис → Deployments).
Прямые вебхуки в Settings → Webhooks **не добавлять**, иначе каждый push задеплоится дважды и без проверок.
Без CI деплой запускается кнопкой **Deploy** в Dokploy.

Переустановка раннера: токен регистрации — GitHub → Settings → Actions → Runners → New self-hosted runner
(или `gh api -X POST repos/leksika1/t-invest-hack/actions/runners/registration-token`), затем на сервере
`sudo -u gha-runner ./config.sh --url https://github.com/leksika1/t-invest-hack --token … --labels tinvest --replace`
и `sudo ./svc.sh install gha-runner && sudo ./svc.sh start`.

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

## Целевая настройка OpenRouter и коллекторов

В Environment сервиса Backend в Dokploy задать ключ `LLM_API_KEY` и явно `LLM_BASE_URL=https://openrouter.ai/api/v1`, `LLM_MODEL=google/gemini-2.5-flash`. Существующий клиент принимает эти три настройки, но POST карточки пока не вызывает модель. В текущем коде base URL/модель по умолчанию пусты; целевые defaults появятся на этапе 3.

На этапе 3 добавить `PUBLIC_BASE_URL` (полный URL сайта Dev/PROD) в настройки и environment обоих backend Compose. Шлюз для OpenRouter передаёт `HTTP-Referer: <PUBLIC_BASE_URL>` и `X-Title: Bond Card MVP`; если URL не задан, не подставлять выдуманный адрес. Заголовки атрибуции необязательны для API. `X-Title` поддерживается как совместимый вариант — [OpenRouter app attribution](https://openrouter.ai/docs/app-attribution). Другим провайдерам эти заголовки не отправлять.

Токены брать из usage. Если есть валидное неотрицательное `usage.cost` (включая 0), использовать его; иначе считать по токенам и тарифам конфига. Сохранять `cost_source=provider/config/unknown`, raw usage и ID ответа. Не складывать стоимость провайдера с расчётной: это альтернативы. См. [OpenRouter usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting). Для резерва перед вызовом всё равно нужны верхние оценки тарифов/max tokens; неизвестный расход не считать нулевым. Цены проверить перед платным прогоном.

Эмбеддинги опциональны: поддержку `/embeddings` выбранным OpenRouter endpoint/моделью **проверить** на этапе 2–3. Нет EMBEDDING_MODEL/конфига или ошибка → FTS с предупреждением. Успешный платный вызов учитывается даже при последующей ошибке индексации.

Коллекторам нужны User-Agent с реальным контактом проекта, задержки, кэш и таймауты; переменные — в README. Контакт пока не задан, уточнить у команды. Официальный Интерфакс включается только с `EDISCLOSURE_API_URL` и `EDISCLOSURE_API_KEY` после реализации адаптера по договорной спецификации. Заполненные переменные не делают заглушку рабочим API. Не скрейпить защищённый сайт, не обходить 403/капчу. Политика источников и кэша — [DATA](DATA.md).

На этапе 2 добавить в оба backend Compose persistent volume `files:/data/files`, миграции перед Uvicorn, в образ — PyMuPDF/Tesseract rus+eng. Том файлов отделён от pgdata и от другого окружения. Текущий nginx (в frontend Dockerfile) ограничивает тело 1 МиБ: согласовать с 30 МиБ на PDF, количеством файлов и лимитом Traefik. Текущие лимиты backend 512 МиБ / 1 CPU проверить на OCR/архивах. Сейчас эти возможности не реализованы.

Архивы коллекторов распаковывать во временный каталог с лимитами байтов/числа файлов/коэффициента сжатия. Запретить вложенные архивы, symlink и выход за каталог. Только PDF идут в общий ingest. Сохранять URL/хеш архива и имя элемента в provenance; при сбое очищать временные файлы. Числовые квоты определить на этапе 2.

Резервная копия должна содержать согласованные Postgres и files-volume. До внешнего запуска проверить восстановление; сроки хранения/очистки пока требуют решения. CI не должен удалять тома Dev/PROD.

## Результат статической уборки этапа 1b

README сокращён, подробности эксплуатации перенесены сюда. [Конкурсная анкета](contest/answers_4-8.md) перемещена без изменения содержимого как исторический документ. Её варианты бота и технологий уточнены текущими ARCHITECTURE/DATA.

Инвентаризация файлов/ссылок не выявила подтверждённо неиспользуемых файлов приложения, которые можно безопасно удалить в этом этапе. `__init__.py` — пакетные файлы Python; lock-файл, overlay e2e и выключенный workflow используются существующим процессом. Код не удалялся.

`not_implemented` действительно есть в схеме и тестах заглушки; замена запланирована на этап 3. Упоминания Telegram-бота — только исторический вариант/граница, Telegram-канал сущфактов Интерфакса — развитие. `APP_PORT` отсутствует в текущих настройках. Порты 18080/55433 — локальные defaults overlay, 28080/55434 — scripts/ci.sh: это разные действующие сценарии. Workflow использует defaults overlay. Логика CI/хуков не меняется.

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

