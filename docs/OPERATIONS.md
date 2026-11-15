# Эксплуатация: Dokploy, CI и хуки

Этапы 2–4: реализованные настройки и команды. Проверки текущего этапа перечислены в DEVELOPMENT; живой OpenRouter не вызывался. Docker daemon в текущей сессии недоступен, поэтому свежий полный прогон PostgreSQL/e2e и деплой не заявляются. Переменные — в [README](../README.md).

Остановить локальный стек без удаления данных:

```bash
docker compose -f docker-compose.yml -f docker-compose.e2e.yml down
```

`down -v` удаляет тома БД; удаляет и files-volume с документами. Не применять к средам с нужными данными.

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
| LLM proxy | `deploy/llm-proxy/docker-compose.yml` | без домена и опубликованных портов | `SUBSCRIPTION_URL`, `LLM_NETWORK` своего Dev/PROD |

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
   `npm audit --audit-level=high`, lint, typecheck, `npm test` (Vitest, без watch) и build. Недоступность сервиса npm audit:
   три попытки, затем предупреждение; найденные уязвимости проваливают CI.
3. **Compose:** доступность Docker и `config -q` для Backend, Frontend и LLM proxy с заглушками
   `POSTGRES_PASSWORD` / `BACKEND_ALIAS` / `LLM_NETWORK`. Внешние сети при config не нужны. Этот этап остаётся и в `--fast` / `--no-e2e`.
4. **PostgreSQL:** `docker-compose.db-tests.yml`, изолированный проект `tinvest-dbtests`, БД `bonds_test` в tmpfs, порт `TEST_DB_PORT` (55435). `pytest -q -m db` с TEST_DATABASE_URL, затем down -v. Шаг выполняется и в --fast/--no-e2e; trap удаляет только этот тестовый проект. В workflow тот же pgvector:pg16 через services с динамическим портом; основной pytest там запускает unit + db.
5. **E2E:** отдельный Compose-проект `tinvest-ci`, сборка, `up -d --wait`, `pytest -q tests/e2e`,
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

## Реализованная конфигурация этапа 2

Оба backend Compose подключают `files:/data/files`, изолированный между Dev/PROD. PDF по sha256, HTTP-кэш в `/data/files/http-cache`; Postgres отдельно в pgdata. Бэкенд-образ включает Tesseract rus+eng, PyMuPDF, ReportLab и Alembic. PDF-отчёт использует встроенные OFL TTF; Pango/GTK для выбранного ReportLab не нужны. Контейнер выполняет `alembic upgrade head` до Uvicorn, один процесс приложения запускает asyncio-worker. В nginx лимит тела увеличен до 160 МиБ; лимит Traefik на живом сервере проверить отдельно.

Основные лимиты и все env — таблица README. На один PDF 30 МиБ, 5 файлов/запрос, 1000 страниц; размер OCR-страницы ≤25 млн пикселей при 150 dpi; timeout OCR 60 с/страницу и всего разбора 300 с. Разбор выполняется subprocess, в Linux также CPU limit 300 с. Ошибка OCR сохраняется на странице и документе; ready нельзя считать документ с нераспознанными страницами.

ZIP у коллекторов и в ручной загрузке: скачивание ≤100 МиБ, распаковано ≤150 МиБ, ≤100 элементов, коэффициент сжатия ≤100; каждый PDF ≤30 МиБ. Проверяются traversal, абсолютные пути, symlink, шифрование и вложенные архивы; извлечение PDF в память, пути архива не используются для записи. RAR возвращает rar_unsupported. Загрузка пользователя принимает PDF/ZIP, до пяти внешних файлов; все файлы проверяются до сохранения. На весь пакет распакованных PDF действуют лимиты 150 МиБ и 100 PDF. Архивное происхождение сохраняется в origins.

Коллекторы: User-Agent `BondCardMVP/0.1 (+https://tinvest.leksika1.ru)`, задержка ≥1 с между запросами домена, timeout 25 с, TTL кэша 3600 с. Хосты разрешены явно, DNS-адрес проверяется и используется напрямую при соединении с проверкой TLS имени; redirects проверяются заново. 403/robots deny/ошибка robots закрывают источник, 404/410 robots трактуется как отсутствие правил. Капчи не обходятся. Условия источников и их поведение описаны в DATA, два реальных демо там же.

`EDisclosureSource` — заглушка: без URL/ключа official_access_required, с ними adapter_not_implemented. Не включать произвольный скрейпинг. Ассистированный режим возвращает страницу поиска и строку ИНН/названия для ручного ввода. Архивы и сторонние CDN вне разрешённых доменов не скачиваются.

Воркер: poll 1 с, lease 180 с, heartbeat через треть lease, максимум 3 попытки восстановления после сбоя процесса. Короткий claim FOR UPDATE SKIP LOCKED коммитится до обработки; отдельная advisory-блокировка сериализует обработку одного PDF при повторах. Старый владелец не может завершить job нового владельца. При рестарте просроченные задачи переигрываются идемпотентно; повторный collect сохраняет пользовательский enabled и origins, повтор ingest готового PDF не создаёт новые страницы/chunks.

FTS russian + GIN активны. Vector DIM по умолчанию 1024, допустимо 1–2000 для HNSW; при начальной миграции DIM берётся из окружения. Изменение DIM после создания БД требует отдельной миграции/переиндексации; простая смена env не меняет существующий столбец. На этапе 3 ingest и запросные эмбеддинги подключены к бюджетному LLMGateway. Нужны модель, ключ и тариф. Отсутствие/ошибка → FTS с предупреждением; уже готовые chunks с пустым/другим embedding не перестраиваются автоматически. После смены модели нужна переиндексация, не смешивайте векторы разных моделей.

Резервные копии должны сохранять согласованные Postgres и files-volume. Реальное восстановление/сроки хранения/квоты диска проверить перед внешним запуском. Просроченный HTTP-кэш обновляется при повторном запросе; общей автоматической очистки и пользовательского удаления документов пока нет. Приложение остаётся общим открытым демо; admin защищён токеном, а создание/загрузка/collect — общим PostgreSQL rate limiter. Правки карточек по-прежнему без аутентификации, как предусмотрено MVP.

## Проверки этапа 2

Обычный `pytest -q` без TEST_DATABASE_URL пропускает db-тесты, без E2E_BASE_URL пропускает e2e. Unit-тесты используют сохранённые ISS/HTML, синтетические русские PDF и fake OCR, не внешние источники. В Windows sandbox временный каталог pytest можно направить внутрь venv:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.venv/pytest-local
```

Для БД использовать только отдельную базу с именем `*_test`: db fixture прогоняет миграции и очищает таблицы тестового приложения. Локально Git Bash из корня:

```bash
docker compose -p tinvest-dbtests -f docker-compose.db-tests.yml up -d --wait
cd backend
TEST_DATABASE_URL=postgresql+psycopg://bonds:ci@127.0.0.1:55435/bonds_test python -m pytest -q -m db
cd ..
docker compose -p tinvest-dbtests -f docker-compose.db-tests.yml down -v
```

`TEST_DB_PORT` меняет локальный тестовый порт. Скрипт CI делает этот прогон и cleanup автоматически; workflow использует динамический порт сервиса. Проверяются миграция (включая повтор), FTS/изоляция выпусков/vector, claim SKIP LOCKED/lease/owner, API и идемпотентный ingest. E2E — HTTP-загрузка → job → разбор → карточка без LLM → PATCH/Markdown/список. Для CI добавлен docker-compose.ci.yml с ISS-fixture на записанных JSON и пустыми ключами LLM/embedding; scripts/ci.sh и workflow используют его третьим файлом. Локальный обычный overlay e2e не подменяет реальный ISS. В самих e2e карточка всегда создаётся после загрузки PDF, поэтому коллекторы не вызываются.

## OpenRouter, бюджет и admin — реализованный этап 3

Формат запроса и ответа сверен с официальными документами: [structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs), [usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting), [app attribution](https://openrouter.ai/docs/app-attribution). Используем Chat Completions response_format.json_schema, usage.cost и заголовки из задания; X-Title поддерживается для совместимости. Это проверка контракта, реальный endpoint с ключом не вызывался.

Defaults кода и обоих Compose: LLM_BASE_URL=https://openrouter.ai/api/v1, LLM_MODEL=google/gemini-2.5-flash. Ключ задаётся в Environment Backend. PUBLIC_BASE_URL даёт HTTP-Referer, X-Title=Bond Card MVP добавляется только для OpenRouter. Заголовки атрибуции необязательны — [официальная документация](https://openrouter.ai/docs/app-attribution). В этой сессии модель не вызывалась.

До платного запуска установить положительный LLM_DAILY_BUDGET_USD (default 10 USD/UTC-день). Для точного имени модели `google/gemini-2.5-flash` тарифы LLM_INPUT_PRICE_PER_1M_USD/LLM_OUTPUT_PRICE_PER_1M_USD по умолчанию — `0.30`/`2.50` USD за 1 млн входных/выходных токенов. Пропущенная переменная, пустая строка или пробелы выбирают этот default; явные числа, включая 0, его переопределяют. Проверить актуальность на [openrouter.ai/models](https://openrouter.ai/models); исходный тариф также указан на [странице Gemini 2.5 Flash](https://openrouter.ai/google/gemini-2.5-flash).

Для другой LLM_MODEL автоматического тарифа нет: задать оба значения явно, иначе шлюз откажет с `llm_prices_not_configured` до резерва/запроса. Оба Compose намеренно передают пустую строку при отсутствии env: Settings разрешает её по имени модели. Числовой `${VAR:-0.30}` в Compose ошибочно назначил бы тариф Gemini другой модели. Эмбеддингам по-прежнему нужен отдельный EMBEDDING_INPUT_PRICE_PER_1M_USD; цена генерации на них не переносится. Фактическое валидное `usage.cost` OpenRouter имеет приоритет над расчётом по токенам.

`GET /api/health` возвращает `llm=configured` только при наличии непустого ключа и обоих тарифов. При отсутствии одного из них — `llm=not_configured`, и существующий фронтенд показывает баннер. Дополнительные `llm_proxy`/`llm_proxy_scheme` описаны ниже. Health не вызывает провайдера и не проверяет остаток дневного бюджета или работоспособность ключа.

Резерв до каждой попытки: верхняя оценка UTF-8 байтов входного payload + 1024 служебных токена, плюс max_tokens выхода, умноженные на тарифы / 1M. PostgreSQL advisory-lock UTC-дня сериализует проверку расходов и незакрытых резервов генерации/эмбеддингов. Стоимость ответа: валидное usage.cost (включая 0), иначе токены/тарифы; неизвестное сохраняет резерв. Не суммировать provider cost и fallback. Дата бюджета — дата создания попытки UTC. Это ограничение по заданным верхним тарифам, не гарантия цены при ошибочно заниженном конфиге; фактическая usage.cost всё равно записывается.

Каждая фактическая попытка — строка llm_calls с job/field, attempt, состоянием, токенами, стоимостью/резервом, длительностью, кодом ошибки и ID ответа. Промпты/ключи не журналируются, usage ограничен числовыми метриками. Прерванная попытка остаётся reserved/unknown до сверки; автоматического возврата неизвестных списаний нет. Проверка бюджета до транспорта, которая запретила вызов, даёт причину у поля и не создаёт фиктивный модельный вызов. Формат usage — [OpenRouter accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).

JSON: сначала упрощённая provider_schema в json_schema; при 400/422 с признаками schema/too many states/response_format/structured — один переход на json_object со схемой в промпте. Ответ всегда проходит полную Pydantic-валидацию; допускается один repair невалидного JSON. 429/5xx/таймаут/сеть — максимум LLM_MAX_RETRIES (default 2), backoff и Retry-After до 60 с; больший Retry-After прекращает текущую попытку, не повторяет раньше срока. Таймаут 45 с, выход 4096 токенов. Шлюз — единственная точка вызовов; SDK-транспорт не выполняет скрытых повторов. Structured output поддерживается не каждым endpoint — [OpenRouter schema](https://openrouter.ai/docs/guides/features/structured-outputs).

HTTP 4xx без usage (в том числе отказ схемы, авторизации/кредитов и 429) даёт cost=0, cost_source=request_rejected, state=failed и освобождает резерв. Если usage содержит стоимость/токены, они учитываются; неполный usage, таймаут и 5xx без стоимости остаются unknown с резервом. Ошибка содержит http_status, безопасные выжимки сообщений OpenRouter/провайдера и provider_name/status; целый metadata.raw и входной текст не сохраняются. В карточке показывается короткая русская причина: проверить схему, ключ, кредиты или доступ соответственно коду.

Это правило применяется к новым попыткам. Исторические unknown-резервы от прежних HTTP 400 автоматически не исправляются: перед новой проверкой модели сверить такие строки с журналом OpenRouter и при необходимости согласованно исправить только подтверждённые отказы без usage. Базу в рамках этого исправления не меняли; массовое обнуление unknown также сняло бы резервы неопределённых списаний. Готовую карточку пересобрать новой версией после исправления.

Admin: ADMIN_TOKEN, заголовок X-Admin-Token, secrets.compare_digest. Пустой серверный токен → 503, отсутствующий/неверный → 401. GET /api/admin/llm-calls и /api/admin/stats показывают UTC-расходы/резервы/ошибки. Тарифы и дневной лимит не доступны для записи через открытый API.

Rate limiter: таблица rate_limit_events, окно 60 с; cards 5, uploads 10, collect 5 на ASGI-адрес клиента. Проверка общая между процессами, 429 + Retry-After; недоступна БД → 503. Поддельный X-Forwarded-For сам не используется. За прокси адрес может быть общим для нескольких пользователей; доверенные proxy-адреса Uvicorn нужно согласовать с реальной топологией, не включать доверие всем заголовкам без проверки.

## Режим контекста и бюджет

Рисковые поля 9–15 используют весь выбранный комплект готовых PDF, если сериализованный текст со всеми метками ≤ EXTRACTION_FULL_CONTEXT_MAX_TOKENS (default 150000 UTF-8 байт консервативной оценки; 0 — только retrieval). При превышении/неполных страницах используется FTS/vector top-k. В пакетном режиме поля 1–8 также получают полный контекст, если комплект укладывается в порог; иначе выполняется один retrieval на группу. Изменение передано в оба Compose, CI workflow не менялся; обычный pytest подхватывает новые проверки.

Первичных извлечений до 15: 8 ISS + 7 рисковых по отдельности. На сохранённых двух демо-комплектах: примерно 1.11–1.21 млн входной **верхней оценки**, максимум 61440 выходных токенов и около $0.49–0.52 суммы резервов без повторов/скидок. Это offline-расчёт по payload, не реальный usage. Default дневного бюджета — $10, чтобы вместить не меньше 10 успешных карточек такого размера без повторов/кэша. Уже заданный LLM_DAILY_BUDGET_USD=1 в Dokploy необходимо изменить вручную, если нужен новый лимит; .env-файлы не редактировались. Подробности расчёта и ограничения — [ARCHITECTURE](ARCHITECTURE.md#полный-контекст-и-стоимость-карточки).

Проверять journal.risk_context_mode (full_document/retrieval/none), risk_context_tokens_upper_bound и фактические calls/usage/cost. Implicit caching проверить на своём endpoint; бюджет не учитывает скидок. После обновления создать новую версию карточки. Сохранённые raw двух диагностик используются как format-regression fixtures; эталоны demo по-прежнему черновики, не доказательство смысловой точности нового промпта.

## LLM через прокси

Основная схема Dev/PROD — **mihomo по URL подписки**, отдельный Compose service «LLM proxy» в Dokploy. Причина — наблюдавшийся на Dev ответ OpenRouter/Cloudflare `403 Access denied by security policy` с российского IP дата-центра. Выход из подписки нужно проверить на доступ к OpenRouter: успешное TCP-соединение не гарантирует, что провайдер принимает этот IP.

### Сети и порядок развёртывания

| Окружение | BACKEND_ALIAS в Backend | LLM_NETWORK в LLM proxy |
|---|---|---|
| Dev | `tinvest-backend-dev` | `tinvest-backend-dev-llm` |
| PROD | `tinvest-backend-prod` | `tinvest-backend-prod-llm` |

Обе внешние bridge-сети уже созданы вручную. Если одна отсутствует, создать **только нужную** на сервере:

```bash
docker network create tinvest-backend-dev-llm
# Для PROD, если отсутствует:
# docker network create tinvest-backend-prod-llm
```

Порядок: **создать/проверить сеть → задеплоить LLM proxy → задеплоить Backend**. Существующие сети, Compose Path и переменные сервисов Dokploy менять не требуется.

1. В сервисе **LLM proxy** оставить Compose Path `deploy/llm-proxy/docker-compose.yml`. В его Environment вставить реальный `SUBSCRIPTION_URL` и соответствующий `LLM_NETWORK` из таблицы. URL должен отдавать совместимую с Clash/mihomo подписку с узлами; правила, DNS и controller из удалённой подписки не импортируются. Секретный URL в репозитории не хранить, в shell-команды не вставлять.
2. Запустить LLM proxy. Файл [docker-compose.yml](../deploy/llm-proxy/docker-compose.yml) подключает **только** выделенную сеть с alias `tinvest-llm-proxy`. Нет `dokploy-network`, host network, опубликованных портов, TUN и NET_ADMIN. Порт 7890 доступен backend по приватной сети; домен и Traefik-маршрут сервису не назначать.
3. Задеплоить Backend с прежним `BACKEND_ALIAS`. Его Compose автоматически подключает сеть `<BACKEND_ALIAS>-llm` и по умолчанию задаёт `LLM_PROXY_URL=http://tinvest-llm-proxy:7890`. Не задавать `SUBSCRIPTION_URL` в Backend — он ему не нужен. Настройки ключа, модели и тарифов LLM остаются отдельными.

Frontend и PostgreSQL к LLM-сети не подключены. Другие проекты не имеют в неё доступа, пока администратор явно не подключит их контейнеры; не добавлять их в эти сети. Одинаковый alias в Dev и PROD разрешается в своей сети, поскольку backend подключён только к LLM-сети своего окружения. Права администратора Docker дают доступ к секретам контейнеров, сама сеть их не ограничивает.

Корневой `docker-compose.yml` для локальной разработки использует прямой доступ (`LLM_PROXY_URL` пуст по умолчанию) и не требует внешних сетей. CI overlay дополнительно принудительно очищает `LLM_PROXY_URL`. `config -q` проверяет конфигурацию без создания/проверки наличия внешней сети — это не доказательство успешного запуска. В `scripts/ci.sh` и `.github/workflows/ci.yml` добавлена одинаковая проверка Compose LLM proxy с фиктивным `LLM_NETWORK`.

### Версия и конфигурация mihomo

Закреплён `metacubex/mihomo:v1.19.33` с manifest digest `sha256:9211361a3ed85305383288d38c05e85862d6d5a590c10007c37fc059cca82e9c`. [GitHub release](https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.33) опубликован **21.10.2026**, [тег Docker Hub](https://hub.docker.com/r/metacubex/mihomo/tags?name=v1.19.33) — в тот же день; проверено 15.11.2026, возраст больше двух недель. Это фиксированный образ, не latest/Alpha. [Dockerfile этой версии](https://github.com/MetaCubeX/mihomo/blob/v1.19.33/Dockerfile) использует Alpine с `/bin/sh` и BusyBox: renderer/healthcheck используют sh, awk и cat, установка envsubst/curl/nc при старте не нужна.

[Шаблон](../deploy/llm-proxy/config.template.yaml) задаёт mixed-port 7890, allow-lan, bind-address `*`, log-level warning, rule mode и единственное правило `MATCH,LLM`. HTTP provider обновляет подписку раз в 3600 секунд, принимает до 8 МиБ. URL-test выбирает узел через запрос `https://openrouter.ai/api/v1/models`, ожидает HTTP 200; проверки выполняются раз в 300 секунд. API/controller выключен, DNS-listener/hijack и TUN выключены. Пустая группа использует `empty-fallback: REJECT`, а узлы типа direct исключены: отсутствующая подписка не включает прямой COMPATIBLE/DIRECT. Поля сверены с [proxy-providers](https://wiki.metacubex.one/en/config/proxy-providers/), [url-test](https://wiki.metacubex.one/en/config/proxy-groups/url-test/) и [парсером групп закреплённой версии](https://github.com/MetaCubeX/mihomo/blob/v1.19.33/adapter/outboundgroup/parser.go).

[render-config.sh](../deploy/llm-proxy/render-config.sh) читает URL из окружения, экранирует одинарные кавычки и вставляет **один YAML scalar**. Нет eval, выполнения shell-подстановок и URL в аргументах процесса. Управляющие символы/пустое значение отклоняются с безопасным сообщением. Отсутствующий SUBSCRIPTION_URL не блокирует `config -q`, но контейнер завершится с понятной ошибкой до запуска mihomo.

[entrypoint.sh](../deploy/llm-proxy/entrypoint.sh) создаёт конфиг с umask 077, удаляет SUBSCRIPTION_URL из окружения дочернего процесса, выполняет `/mihomo -t`, затем запускает mihomo. Root FS read-only; рабочая папка `/root/.config/mihomo` с конфигом/кэшем подписки — tmpfs 32 МиБ, `/tmp` — tmpfs 8 МиБ. При пересоздании кэш исчезает и подписка загружается заново. Лимиты сервиса: 128 МиБ RAM, 0.5 CPU, 64 PID, no-new-privileges, все capabilities удалены.

Ошибки mihomo могут содержать URL подписки. Поэтому stdout/stderr самой программы, включая `-t`, направлены в `/dev/null`; в Dokploy остаются только общие сообщения entrypoint без URL. Не включать `set -x`, debug-логирование и вывод сгенерированного конфига. Если нужна диагностика, использовать HTTP-проверку ниже и безопасные health/admin-поля. В Docker inspect и Environment Dokploy подписка доступна администраторам — считать её секретом.

[Healthcheck](../deploy/llm-proxy/healthcheck.sh) читает `/proc/net/tcp{,6}` и проверяет TCP LISTEN на 7890. Он подтверждает **только открытый порт**, не загрузку подписки и не работоспособность узла. В этой сессии Docker-контейнер и реальная подписка не запускались; их проверяет владелец на сервере.

### Проверка, отказ и ротация

После запуска проверить из контейнера Backend (заменить только имя контейнера; ключ OpenRouter и URL подписки не нужны):

```bash
docker exec <backend-container> python -c 'import httpx; from app.config import get_settings; s=get_settings(); r=httpx.get("https://openrouter.ai/api/v1/models", proxy=s.llm_proxy_url or None, trust_env=False, timeout=30); print("OpenRouter HTTP", r.status_code); r.raise_for_status()'
```

Ожидается HTTP 200. Вывод не содержит тела ответа, SUBSCRIPTION_URL или proxy URL. Эта публичная проверка не является платным вызовом генерации. Затем проверить `/api/health`: `llm_proxy=configured`, `llm_proxy_scheme=http`; это только настройки. Для проверки извлечения собрать новую версию карточки явным действием и смотреть journal/models/tokens/errors. Ключ, тарифы и дневной бюджет должны быть настроены отдельно.

Нет контейнера/сети, DNS-имя `tinvest-llm-proxy` не разрешается, соединение отказано или истёк connect timeout → `llm_proxy_error` / «Прокси LLM недоступен». Backend остаётся доступен, поля 1–8 сохраняют доступное ISS-значение как extracted/moex_iss, текстовые 9–15 получают needs_review с причиной; код есть в journal.warnings и журнале попыток. Сбой туннеля может быть между прокси и провайдером — по одному коду точный участок не определить. Read timeout после подключения остаётся `llm_timeout`, HTTP 403 провайдера — `llm_access_denied`. Неизвестная стоимость сетевого сбоя сохраняет резерв; HTTP-отказы 4xx без usage освобождают его по правилу выше. Эмбеддинги при отказе сохраняют fallback на FTS. Автоматического прямого обхода заданного прокси нет.

Для ротации заменить SUBSCRIPTION_URL **в Environment нужного LLM proxy** и выполнить Redeploy/recreate контейнера: простой restart не применяет новое окружение. Tmpfs очистится, новая подписка будет загружена немедленно. Backend перезапускать не нужно, если alias/порт не меняются. Повторить публичную HTTP-проверку, затем собрать новую карточку; готовые прежние версии автоматически не пересчитываются. При изменении состава узлов по тому же URL provider обновляет их раз в час.

LLM_PROXY_URL по-прежнему поддерживает `http://`, `https://`, `socks5://`, `socks5h://` и авторизацию в userinfo. Схема https означает TLS до самого прокси, а HTTPS-модель через обычный HTTP-прокси использует CONNECT. Спецсимволы credentials кодировать (`@` → `%40`, `:` → `%3A`, `%` → `%25`). HTTPX настроен с `trust_env=False`: глобальные HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY не меняют маршрутизацию; ISS/коллекторы остаются прямыми. Health/admin stats показывают только состояние и схему, без endpoint/userinfo. Для намеренного прямого доступа использовать Compose override с `LLM_PROXY_URL: ""`; пустая переменная Environment без override выбирает серверный default прокси. На этом сервере прямой OpenRouter заблокирован, штатный режим — mihomo.

### Альтернативы Gluetun/Xray

Сохранены в [alternatives/compose.gluetun.yml](../deploy/llm-proxy/alternatives/compose.gluetun.yml) и [alternatives/compose.xray.yml](../deploy/llm-proxy/alternatives/compose.xray.yml), JSON-шаблон Xray — [config.xray.example.json](../deploy/llm-proxy/alternatives/config.xray.example.json). Это отдельные альтернативы **вместо mihomo**; не запускать их вместе с ним с тем же alias. Обе также требуют выделенную LLM_NETWORK, к dokploy-network не подключаются и не публикуют порты.

Gluetun: свой WireGuard-профиль вне Git, WG_CONFIG_PATH, LLM_PROXY_USER/PASSWORD в Environment; HTTP proxy с авторизацией на `tinvest-llm-proxy:8888`. Для OpenVPN заменить VPN_TYPE/mount на свой `.ovpn`, задать OPENVPN_CUSTOM_CONFIG и нужные OPENVPN_USER/PASSWORD. Проверить версию образа и профиль по [официальной документации Gluetun](https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/custom.md).

Xray: свой серверный JSON из шаблона, XRAY_CONFIG_PATH, проверить tag/digest, UUID/endpoint/SNI/transport, чтение файла UID/GID образа (официальный nonroot 65532). HTTP на 8080 или SOCKS на 1080 с авторизацией; LLM_PROXY_URL задать явно, например `socks5h://PROXY_USER:PROXY_PASSWORD@tinvest-llm-proxy:1080`. Шаблон — TCP+TLS, для REALITY/WS/gRPC нужен конфиг своего провайдера. Реальные секреты в Git не добавлять. Документация: [образ Xray](https://github.com/XTLS/Xray-core/blob/main/.github/docker/Dockerfile), [SOCKS inbound](https://xtls.github.io/en/config/inbounds/socks.html), [VLESS outbound](https://xtls.github.io/en/config/outbounds/vless.html).

## Асинхронная карточка и проверки

POST /api/cards с {isin} → 202 {card_id,job_id,status}; затем GET /api/jobs/{job_id} и /api/cards/{card_id}. Новая версия фиксирует выбор документов; поздний enabled не меняет текущую карточку. Без загруженных/ранее собранных документов job сама запускает автосбор. Ручной комплект не требует автосбора; выключение всех старых PDF разрешает ISS-only режим. Ошибка OCR отдельного PDF — предупреждение и исключение его текста, не ложный факт.

Без LLM_API_KEY (или в ISS-only без PDF) прямые доступные ISS-поля extracted/moex_iss, без PDF-цитаты и без verified_iss. При отказе шлюза (нет тарифов, бюджет, таймаут, ошибка провайдера/клиента, невалидный JSON после повторов) поля 1–8 также сохраняют доступные значения ISS; недоступное значение остаётся not_found/iss_unavailable. Код отказа добавляется в journal.warnings. Поля 9–15 без ключа — not_found/llm_not_configured, при остальных отказах — needs_review с кодом и русским reason_message. Не подтверждена документальная цитата/числовой параметр: рисковые значения скрываются с needs_review, а поля 1–8 показывают доступный ISS как extracted с reason=iss_not_confirmed. Совпадение части параметров графика помечается iss_partial_confirmation; это не сверка всего графика. Исправление хранится отдельно от машинного ответа. Проверка человеком и эксперимент качества всё ещё нужны; готовность карточки означает окончание конвейера.

После установки исправления собрать новую версию карточки явным POST /api/cards: готовые прежние версии сохраняют исходный результат и автоматически не пересчитываются.

Для самостоятельного offline e2e использовать три Compose-файла, включая docker-compose.ci.yml; при старте задать тестовый POSTGRES_PASSWORD через окружение. Не использовать CI overlay для обычного Dev/PROD: он намеренно подменяет ISS, отключает ключи и задаёт тестовый admin-токен. Полный bash scripts/ci.sh выполняет это изолированно и очищает только свои тестовые проекты.

Миграция 0002 добавляет journal/job ссылку карточки, reason/source/reviewed_at/not_found поля, состояние/резерв/attempt вызова и таблицу rate limiter. Миграция при старте прежняя: alembic upgrade head до Uvicorn. Новые настройки переданы в оба backend Compose, .env* не редактировались.

## Разработка без Docker

Бэкенд (нужен Postgres; адрес задаётся `DATABASE_URL`, например
`postgresql+psycopg://bonds:пароль@127.0.0.1:55433/bonds` — база из `docker-compose.e2e.yml`):

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt      # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest
.venv/bin/python -m alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Фронтенд (dev-сервер проксирует `/api` на `http://localhost:8000`):

```bash
cd frontend
npm install
npm run dev
npm test             # Vitest + Testing Library, локальные fixtures, без внешней сети
npm run lint
npm run typecheck
npm run build
```

Нужен Node.js 22.13+ (или современный 24 LTS); workflow/Docker используют 22. Для воспроизводимой установки после clone — npm ci. Vitest подключён к Vite config, jsdom и @testing-library/jest-dom; [Vitest](https://vitest.dev/guide/), [Testing Library](https://testing-library.com/docs/react-testing-library/setup/).

Для временной проверки против Dev без изменения .env: Bash — `DEV_API_TARGET=https://dev.tinvest.leksika1.ru npm run dev`; PowerShell — `$env:DEV_API_TARGET='https://dev.tinvest.leksika1.ru'; npm run dev`. Прокси применяется только к Vite dev server, браузер обращается на тот же origin /api. Локальный фронтенд должен работать с backend этапа 3; старый синхронный POST карточки этапа 2 несовместим. Ни модельные ключи, ни admin-токен вводить в UI не требуется.

Клавиатура: ссылка «К содержимому», подписанные inputs, Tab/Enter, раскрываемые quotes/journal, видимый фокус. Исправления — формы для дат/Decimal-строк/списков, сервер валидирует value; машинные поля не подменяются. Экспорт получает PDF или Markdown через fetch/blob; основное действие — PDF. Новая версия карточки создаётся только явным нажатием кнопки. Хранимые PDF нельзя скачать через несуществующий локальный маршрут: ссылки открывают только исходные HTTP(S) URL при наличии origins.

В frontend/Dockerfile сохранены try_files и multipart-лимит 160m; style-src теперь только 'self' (локальный внешний CSS, без inline-стилей). Проверка production bundle с таким же CSP выполнена в локальном Chromium на тестовых ответах API, не в nginx/Docker. Развёрнутый nginx и полный стек проверяются общим CI.


## Пакетное извлечение и Google fallback

Новые карточки делают два пакета (ISS 8 полей + риски 7) вместо 15 запросов. `LLM_FALLBACK_MODELS` прокинут в оба Compose: CSV моделей в порядке попыток, пусто выбирает preset (Google: `gemini-3.1-flash-lite`, OpenRouter: ничего). Для полного отключения задайте `,`. Неизвестная модель требует зарегистрированного тарифа в DEFAULT_LLM_PRICES: без него шлюз отказывает до отправки. Цена lite пока консервативная как flash, **проверить** перед использованием платного тарифа. `.env*` и CI-файлы не менялись.

429 с дневной квотой / RetryInfo >60 с или 404 о недоступной модели переключает модель внутри текущей операции. Короткий 429 повторяется на той же модели с ожиданием RetryInfo/Retry-After до 60 с и лимитом LLM_MAX_RETRIES. Успешная запасная модель остаётся активной до конца сборки. Ищите фактические модели в journal.response_models (успешные ответы), journal.models (все попытки) / llm_calls.model, число попыток генерации в journal.generation_calls, токены в tokens_in/out. Дневная квота Google содержит в безопасной ошибке quota_id/quota_value/retry_after_seconds и понятную причину со временем сброса; сырые тела ответов/ключи не сохраняются.

LLM_MAX_OUTPUT_TOKENS управляет одиночными ответами; пакет использует не менее 12288 токенов выхода для всех полей и цитат. Этот максимум учитывается в бюджетном резерве до вызова. Обычный поток — 2 запроса генерации, с одним schema/model fallback или исправлением поля — 3; при нескольких ошибках, повторах сети или embeddings запросов больше. Если Google не вернул usage.cost, cost_source=google_estimate и journal.cost_note помечает оценку, которая фактически может быть 0 на free tier. Данные usage.cost, включая 0, имеют приоритет. Точные токены/цена на реальной модели требуют проверки стенда; unit-тесты используют FakeTransport.
