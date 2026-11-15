# HTTP API

## Статус контракта

Реализованы этапы 2–3: issues/collect/documents/jobs, асинхронные карточки/правки/PDF и Markdown и admin API. POST /api/cards теперь возвращает **202 с card_id/job_id**, а не синхронную заглушку. В готовой карточке ровно 8 ISS + 7 рисковых полей. Swagger: /api/docs, /api/openapi.json. Примеры синтетические, кроме демо в DATA.

Контракт этапов 2–3 используется SPA этапа 4; локальный код бэкенда — источник истины. Изменения пагинации, вложенных value и ошибок должны синхронно отражаться во фронтенде и его тестах.

## Общие правила

- JSON UTF-8; UUID для сущностей, даты `YYYY-MM-DD`, время UTC ISO 8601; Decimal как строки. Идентификаторы в примерах условные.
- ISIN обрезается/переводится в uppercase; пока сохраняется проверка каркаса `RU` + 9 букв/цифр + цифра. Контрольная сумма пока не проверяется.
- Источник документа: физическая `page` с 1, `clause` строка или null. `document_id` обязан принадлежать входному snapshot карточки.
- Пагинация списков (предложение): `limit=20`, максимум 100, `offset=0`, ответ `{items,limit,offset,total}`, порядок `created_at DESC, id DESC`.
- Остальные методы открыты; admin защищён `X-Admin-Token`. Общий демо-доступ означает, что правки не атрибутируются аутентифицированному пользователю.
- Ограничения этапа 2: 30 МиБ/PDF, 100 МиБ/ZIP, 5 PDF/ZIP на запрос, общий лимит тела = max(MAX_ARCHIVE_BYTES, MAX_UPLOAD_BYTES × MAX_UPLOAD_FILES) + 1 МиБ, включая chunked; nginx 160 МиБ. Rate limit в PostgreSQL: окно 60 с, cards=5, uploads=10, collect=5 на ASGI-адрес клиента; 429 с Retry-After. Поддельный X-Forwarded-For самостоятельно не учитывается.

## Маршруты

| Метод | Путь | Успех | Назначение |
|---|---|---|---|
| GET | `/api/health` | 200 | Состояние приложения/БД, наличие LLM-конфига |
| GET | `/api/iss/{secid}` | 200 | Существующий прокси description |
| POST | `/api/issues/{isin}/collect` | 202 | Job: ISS → ПРАЙМ → СКРИН → официальный Интерфакс |
| GET | `/api/issues/{isin}/documents` | 200 | Документы, provenance, выбор, разбор и состояния источников |
| PATCH | `/api/issues/{isin}/documents/{document_id}` | 200 | Включить/выключить документ для следующей сборки |
| POST | `/api/issues/{isin}/documents` | 202 | PDF или ZIP с PDF, ingest-job |
| GET | `/api/issues/{isin}` | 200 | Шапка, ISS со временем снимка, документы и ошибки |
| POST | `/api/cards` | 202 | Создать версию карточки и build-job |
| GET | `/api/jobs/{id}` | 200 | Состояние, шаг, прогресс, результат/ошибка |
| GET | `/api/cards/{id}` | 200 | Карточка с журналом и правками |
| GET | `/api/cards?isin=...` | 200 | Последние карточки, ISIN необязателен |
| PATCH | `/api/cards/{id}/fields/{code}` | 200 | Подтвердить/исправить поле |
| GET | `/api/cards/{id}/export.md` | 200 | Markdown готовой карточки |
| GET | `/api/cards/{id}/export.pdf` | 200 | A4 PDF готовой карточки, application/pdf |
| GET | `/api/admin/llm-calls` | 200 | Журнал попыток LLM/embedding, фильтры job_id, date, error |
| GET | `/api/admin/stats` | 200 | UTC-дневной бюджет/расходы, ошибки и задачи |

## Текущие служебные ответы

`GET /api/health` (формат уже реализован):

```json
{"status":"ok","db":"ok","llm":"not_configured","llm_proxy":"none","llm_proxy_scheme":null,"version":"0.1.0"}
```

При ошибке БД HTTP пока остаётся 200, тело `status=degraded, db=error`. `llm=configured` означает наличие непустого ключа и обоих тарифов выбранной модели, включая defaults Gemini Flash. Без ключа/тарифов — `llm=not_configured`. Health не вызывает провайдера и не проверяет его доступность или остаток бюджета.

`llm_proxy` — `configured` или `none`; `llm_proxy_scheme` — `http`, `https`, `socks5`, `socks5h` либо `null`. Хост, логин, пароль и полный URL не возвращаются. Это конфигурация, а не проверка соединения с прокси. Ошибка установки соединения/туннеля прокси даёт `llm_proxy_error` с reason_message «Прокси LLM недоступен»; сохраняются доступные ISS-поля, текстовые поля получают needs_review. Код есть в journal.warnings и журнале попыток LLM.

`GET /api/iss/RU000A10AXF7`: текущий ответ `{secid,description}`, ключи description зависят от ISS. Неверный secid → 422, пустое описание → 404, ошибка сети/формата ISS → 502. Bondization будет внутренней частью issue-сервиса, текущий прокси не объявляется его реализацией.

## Документы и выпуск

### Автосбор и ассистированный режим

```bash
curl -X POST http://127.0.0.1:18080/api/issues/RU000A10AXF7/collect
```

Тело не требуется; произвольные URL и список обходных источников не принимаются. Ответ 202:

```json
{"isin":"RU000A10AXF7","job_id":"77777777-7777-4777-8777-777777777777","status":"queued"}
```

Job `type=collect`: шаги `resolve_iss/prime/skrin/edisclosure/parse/finalize`, порядок фиксирован. Сначала ISS/ИНН, затем все разрешённые источники; ошибка одного не отменяет другие. По завершении job.result содержит `document_ids`, `sources`, `assisted`, `warnings`; источники используют состояния из DATA, отдельно от queued/running/succeeded/failed всей job. Обнаруженные PDF разбираются общим ingest; collect завершается после окончания этого шага и содержит ошибки документов. Частичный/пустой результат — succeeded с предупреждениями, глобальный сбой — failed. Повторный POST во время активной collect-job выпуска предлагает возвращать её ID (202), не плодить конкурентные скачивания; после завершения новый POST запускает обновление с кэшем. Rate limit обязателен и для collect.

`GET /api/issues/RU000A10AXF7/documents?limit=20&offset=0` (синтетический сокращённый список):

```json
{
  "items":[{
    "id":"11111111-1111-4111-8111-111111111111","filename":"decision.pdf","type":"decision",
    "enabled":true,"status":"ready","pages":2,"ocr_pages":0,"error":null,
    "sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "origins":[{"source":"prime","source_url":null,"download_url":null,"published_at":null,"fetched_at":"2026-11-15T09:00:00Z","archive":null}]
  }],
  "limit":20,"offset":0,"total":1,
  "sources":[
    {"source":"prime","status":"succeeded","documents_found":1,"message":null},
    {"source":"skrin","status":"empty","documents_found":0,"message":"Документы не найдены"},
    {"source":"edisclosure","status":"official_access_required","documents_found":0,"message":"Нужен официальный доступ"}
  ],
  "assisted":{"source":"edisclosure","search_url":null,"query":"ИНН из ISS после проверки маппинга","mode":"manual_query","message":"Маршрут поиска по ИНН проверить на этапе 2; скачайте PDF вручную и загрузите"},
  "active_collect_job_id":null,"warnings":[]
}
```

URL null сокращает синтетический пример; в реализации коллекторы сохраняют проверенные source/download URL, реальные примеры — DATA. В реализованном коллекторе provenance обязан содержать реальные source/download URL; у upload они null. published_at неизвестна → null с подписью «дата публикации неизвестна», fetched_at её не заменяет. Для коллекторского архива `archive={url,sha256,entry_name}`, для пользовательского — `archive={url:null,filename,entry_name}`. Если один PDF найден в двух источниках, origins сохраняет оба, документ один. Статусы sources — результат последнего сбора с временем проверки в реализации, отсутствие сбора — sources пустой массив.

На этапе 2 ассистированный режим возвращает https://www.e-disclosure.ru/poisk-po-kompaniyam и query=ИНН (либо название/ISIN). Автозаполнение параметром ИНН не заявляется; запрос нужно вставить в форму вручную. Если проверена только страница поиска — search_url на неё, query=ИНН для копирования, mode=manual_query. Если подтверждён deep link, mode=prefilled_query и URL содержит URL-encoded ИНН. ИНН отсутствует → query=название и предупреждение о необходимости проверки эмитента. Контракт не предполагает обход капчи или передачу её cookies backend.

Без EDISCLOSURE_API_URL/KEY — official_access_required; с ними, но без реализованного договорного адаптера — adapter_not_implemented. Нельзя выдавать фиктивный успех. blocked означает robots/403/капчу; failed — техническую ошибку. Источники и частичные результаты видны даже при невозможности автосбора; загрузка всегда доступна.

### Выбор документов

```bash
curl -X PATCH http://127.0.0.1:18080/api/issues/RU000A10AXF7/documents/11111111-1111-4111-8111-111111111111 \
  -H 'Content-Type: application/json' -d '{"enabled":false}'
```

Ответ 200 — объект документа с `enabled=false`. Неизвестный документ/принадлежность другому выпуску → 404; неизвестные поля тела → 422. Файл, origins и старые карточки не удаляются. Изменение влияет только на следующий snapshot. Новые документы по умолчанию enabled=true, дубликат/повторный collect сохраняет пользовательский выбор. ready-карточка показывает фактически использованный snapshot, а не текущий набор флагов.

### Загрузка

Пример Bash/curl (файлы — подготовленные пользователем PDF):

```bash
curl -X POST http://127.0.0.1:18080/api/issues/RU000A10AXF7/documents \
  -F 'files=@decision.pdf;type=application/pdf' \
  -F 'files=@amendment.pdf;type=application/pdf' \
  -F 'document_types=decision' -F 'document_types=amendment'
```

`document_types` — повторяющееся multipart-поле, соответствует files по порядку; если отсутствует, все типы `other`, несовпадение количества → 422. Типы: `decision/prospectus/amendment/material_fact/other`. Не задавать JSON Content-Type для multipart.

```json
{
  "isin":"RU000A10AXF7",
  "documents":[
    {"id":"11111111-1111-4111-8111-111111111111","filename":"decision.pdf","type":"decision","status":"queued","duplicate":false},
    {"id":"22222222-2222-4222-8222-222222222222","filename":"amendment.pdf","type":"amendment","status":"queued","duplicate":false}
  ],
  "job_id":"33333333-3333-4333-8333-333333333333"
}
```

Повтор sha256 в выпуске возвращает тот же документ и `duplicate=true`, повторно не парсится. Предложение для пакета: сначала валидировать все файлы; ошибка одного не создаёт частично принятый пакет. Ошибка парсинга после приёма отражается в job и документе. Для пакета из одних готовых дубликатов допускается сразу `succeeded` ingest-job, формат ответа тот же.

`GET /api/issues/RU000A10AXF7`:

```json
{
  "isin":"RU000A10AXF7","secid":"RU000A10AXF7",
  "header":{"issuer":"Синтетический эмитент","inn":null,"name":"Тестовый выпуск","listing_level":null},
  "iss":{"status":"available","fetched_at":"2026-11-15T09:00:00Z","description":{},"bondization":{}},
  "documents":[{"id":"11111111-1111-4111-8111-111111111111","type":"decision","filename":"decision.pdf","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","status":"ready","pages":2,"ocr_pages":0,"error":null}],
  "warnings":[]
}
```

Здесь пустые ISS-блоки сокращают пример, не являются реальной fixture. Полный список документов с enabled/origins/sources — отдельный GET documents; краткий список в issue также должен включать enabled/origins. Предложение: GET issue может создать/обновить локальное представление по ISS; неизвестный и в ISS, и локально выпуск → 404. Если документы уже есть, а ISS временно недоступен, вернуть 200 с `iss.status=unavailable` либо `stale`, временем последнего снимка и предупреждением. Без сохранённых данных при сбое ISS — 502. POST загрузки не требует успешного ISS: принимает валидный ISIN и создаёт локальный issue; это сохраняет ручной путь при сбое внешних источников.

## Асинхронная сборка

```bash
curl -X POST http://127.0.0.1:18080/api/cards \
  -H 'Content-Type: application/json' -d '{"isin":"RU000A10AXF7"}'
```

```json
{"card_id":"44444444-4444-4444-8444-444444444444","job_id":"55555555-5555-4555-8555-555555555555","status":"queued"}
```

При POST фиксируются enabled=true документы и их ID. Поздний выбор влияет только на следующую версию. Активный отдельный collect → 409 collection_in_progress. Queued/failed PDF перед сборкой повторно разбираются; нераспознанные документы отмечаются в журнале, их фрагменты не выдаются как надёжные. Если документов ещё вообще нет и сбор не выполнялся, build-job сам запускает collect. Загруженный вручную комплект обрабатывается без принудительного автосбора. Исключение всех ранее загруженных документов означает ISS-only режим. Повтор POST создаёт новую версию/расходы; UI не должен автоматически повторять его.

`GET /api/jobs/55555555-5555-4555-8555-555555555555`:

```json
{
  "id":"55555555-5555-4555-8555-555555555555","type":"build_card","status":"running",
  "step":"extract_fields","progress":60,"processed_fields":9,"total_fields":15,
  "card_id":"44444444-4444-4444-8444-444444444444","error":null,
  "created_at":"2026-11-15T09:01:00Z","started_at":"2026-11-15T09:01:01Z","finished_at":null
}
```

Общие состояния `queued/running/succeeded/failed`; шаги `resolve_iss/prime/skrin/edisclosure/ingest/parse/index/retrieve/extract_fields/verify/finalize` применяются по типу job. Ошибка: `error={code,message}`, без traceback и секретов. HTTP 200 для найденной failed-job. SPA опрашивает GET с интервалом 1.5–5 с, сетевые ошибки/429/5xx повторяет с backoff и Retry-After; терминальное состояние/смена маршрута прекращают polling. POST автоматически не повторяется. Прогресс не обещает время завершения.

Document DTO пока не содержит размер сохранённого PDF. UI показывает страницы, OCR, даты публикации/получения и все origins; размер выбранных для загрузки файлов известен браузеру и отображается до отправки. Даты публикации null не заменяются датой скачивания.

## Карточка и поле

`GET /api/cards/{id}` возвращает `{id,isin,version,status,created_at,updated_at,header,iss_fields,risk_fields,journal,disclaimer}`. `status=processing/ready/failed`; в готовой карточке ровно 8 + 7 полей с кодами CARD_FIELDS. До готовности поля могут отсутствовать. Пример **одного** результата, не целой карточки:

```json
{
  "code":"face_value",
  "value":{"amount":"1000.00","currency":"RUB"},
  "quote":"Номинальная стоимость каждой облигации составляет 1 000 российских рублей.",
  "document_id":"11111111-1111-4111-8111-111111111111","page":1,"clause":"3.1",
  "source":{"kind":"document","name":"decision.pdf","url":null},
  "evidence":[],"confidence":0.98,"not_found":false,
  "status":"verified_iss","iss_value":{"amount":"1000.00","currency":"RUB"},
  "reason":null,"user_status":null,"user_value":null
}
```

`evidence` — объекты `{quote,document_id,page,clause,value_path}` для составных полей/графиков. Основная цитата относится к элементу 0, для остальных нужны JSON-pointer пути вида /events/1, /items/1, /payments/1. Недостаточное покрытие скрывает документальное значение; у рисков это needs_review, а у полей 1–8 при наличии ISS — extracted/moex_iss с iss_not_confirmed. Поле reason содержит стабильный код, reason_message — русский текст. Экстрактор также возвращает ambiguous: true при замеченном противоречии редакций; это даёт needs_review/conflicting_sources. `confidence` число 0–1 или null, не калиброванная вероятность. Сырой not_found=true с пустым контейнером нормализуется в value=null до валидации; публичный not_found=false у ISS-fallback означает доступное биржевое значение. CouponSchedule дополнен необязательным total_payments — явно указанным общим числом купонов.

Варианты поведения той же оболочки:

| Случай | Публичный результат |
|---|---|
| Несовпадение ISS | `status=mismatch_iss`, value документа и iss_value разные; оба показываются рядом с флагом |
| Нет цитаты/цитата не на указанной странице | Риски: `value=null`, needs_review/quote_not_verified. Поля 1–8 с доступным ISS: extracted/moex_iss, reason=iss_not_confirmed, без PDF-цитаты |
| Подтверждено подмножество параметров графика | verified_iss/iss_partial_confirmation; это не проверка всех выплат. Полный iss_value показывается рядом с документальным результатом |
| Условие не найдено | `value=null`, `not_found=true`, `status=not_found`, `reason=not_in_documents` |
| Нет ключа LLM, текстовое поле | `value=null`, `status=not_found`, `reason=llm_not_configured` |
| Прямой ISS без PDF | `status=extracted`, `source.kind=moex_iss`, source.url — путь снимка ISS, quote/document_id/page/clause/confidence = null; value из ISS |
| Отказ LLM: тарифы, лимит, таймаут, провайдер | Поля 1–8: доступное ISS-значение `extracted/moex_iss`, иначе `not_found/iss_unavailable`. Поля 9–15: `value=null`, `status=needs_review`, reason — код отказа; он также в `journal.warnings`. Не делать вывод об отсутствии условия |

У ready-карточки дополнительно `documents`: снимок фактически использованных документов с filename/type/sha256/origins (включая даты/URL и upload). Кнопка источника поля связывается с этим snapshot, не с будущей редакцией документа. Имена файлов, страницы и пункты сохраняются как прежде.

Синтетический пример журнала:

```json
{"documents_used":1,"pages":2,"ocr_pages":0,"models":["configured-model"],"tokens_in":1200,"tokens_out":300,"cost_usd":"0.002000","duration_ms":4200,"warnings":[],"errors":[]}
```

Цена в примере условна. Неизвестная стоимость → null и предупреждение. Шлюз OpenRouter приоритетно использует usage.cost, иначе токены и тарифы; admin-элемент дополняется cost_source, usage и provider_response_id. Ошибки отдельных полей могут сосуществовать с `ready`.

`GET /api/cards?isin=RU000A10AXF7&limit=20&offset=0` возвращает envelope пагинации с краткими `{id,isin,version,status,created_at,updated_at}`, без полного текста цитат.

## Правки и экспорт

```bash
curl -X PATCH http://127.0.0.1:18080/api/cards/44444444-4444-4444-8444-444444444444/fields/face_value \
  -H 'Content-Type: application/json' -d '{"user_status":"confirmed"}'
```

Исправление: `{"user_status":"corrected","user_value":{"amount":"1000.00","currency":"RUB"}}`. Для confirmed новое значение запрещено, для corrected обязательно и валидируется по типу поля. Ответ 200 — полная оболочка изменённого поля, машинные данные сохранены. Подтверждение поля без значения → 409, исправление возможно с явной пометкой пользовательского источника. Карточка не готова → 409. В MVP последняя правка побеждает; конкурентное редактирование/аудит всех редакций — развитие, не скрытая гарантия.

`GET /api/cards/{id}/export.md` → `Content-Type: text/markdown; charset=utf-8`, `Content-Disposition: attachment`. Шапка, все 15 полей, оба значения при mismatch, цитаты/источники, документы с origins/датами/URL, правки и исходный статус, журнал и дисклеймер. Для скрытого value — причина вместо неподтверждённого ответа. До готовности → 409. Markdown экранирует имена файлов и текст документа.

## Административный журнал

```bash
curl http://127.0.0.1:18080/api/admin/stats -H "X-Admin-Token: ${ADMIN_TOKEN}"
```

Сравнение через `secrets.compare_digest`; отсутствующий/неверный токен → 401, пустой серверный ADMIN_TOKEN закрывает доступ (503 `admin_not_configured`). Не передавать секрет в URL.

`GET /api/admin/llm-calls?date=2026-11-15&error=true&limit=20&offset=0` → envelope списков; элемент:

```json
{"id":"66666666-6666-4666-8666-666666666666","job_id":"55555555-5555-4555-8555-555555555555","field_code":"covenants","purpose":"extraction","attempt":1,"model":"configured-model","tokens_in":null,"tokens_out":null,"cost_usd":null,"duration_ms":30000,"error":{"code":"llm_timeout","message":"Истекло время ожидания модели"},"created_at":"2026-11-15T09:01:31Z"}
```

`GET /api/admin/stats?date=2026-11-15` (date по умолчанию текущий UTC-день):

```json
{"date":"2026-11-15","timezone":"UTC","spent_usd":"0.002000","reserved_usd":"0.010000","daily_budget_usd":"1.00","llm_proxy":"configured","llm_proxy_scheme":"socks5h","unknown_cost_calls":1,"calls":3,"errors":1,"jobs":{"queued":0,"running":1,"succeeded":1,"failed":0}}
```

Числа иллюстративны. Stats не включает ключи/промпты. При неизвестных ценах нельзя объявлять остаток бюджета достоверным.

У ошибки попытки дополнительно возможны `http_status`, `openrouter_message`, `provider_name`, `provider_message`, `provider_status`. Это ограниченные и очищенные выжимки, полный `metadata.raw`/тело запроса не возвращается. Русские причины полей включают `llm_schema_rejected`, `llm_auth_error`, `llm_insufficient_credits`, `llm_access_denied`, `llm_model_not_found`, `llm_request_too_large`, `llm_request_rejected`, `llm_rate_limited`. HTTP 4xx без usage имеет cost_source=request_rejected, cost_usd="0", state=failed и освобождённый резерв; unknown означает, что стоимость не установлена.

## Ошибки

Бизнес-ошибки могут иметь вид `{"detail":{"code":"collection_in_progress","message":"Дождитесь завершения текущего автосбора"}}`. Стандартный FastAPI 422 сохраняет массив detail; фронт обязан обрабатывать оба формата. Текущие маршруты также могут возвращать detail строкой.

| HTTP | Причина |
|---|---|
| 401 | Нет/неверный admin-токен |
| 404 | Неизвестный ID, выпуск или код поля |
| 409 | Карточка не готова, выполняется отдельный collect или другой конфликт состояния |
| 413 | Превышен лимит загрузки |
| 415 | Файл не PDF / неподдерживаемый формат |
| 422 | Неверный ISIN, тип документа, структура value или query |
| 429 | Rate limit; `Retry-After` |
| 502 | Недоступен ISS при отсутствии пригодного снимка |
| 503 | БД/очередь недоступны либо admin не настроен |

Ошибка модели после принятия сборки отражается в job/поле/журнале, а не ретроспективным HTTP-кодом POST. Бюджетные ограничения не обходятся повтором запросов.

## Уточнения реализованного этапа 2

GET issue получает ISS только при первом обращении к отсутствующему локальному выпуску. Если issue уже создан загрузкой, GET возвращает сохранённое состояние без сети; обновление ISS запускается через collect. Так ручная загрузка и e2e работают без внешних API. Raw ответы: iss.description содержит полный ответ securities/{isin}, iss.emitter — emitters/{id}, iss.search — fallback поиска, iss.bondization — графики; normalized содержит fields и warnings. Это не плоский description-клиент старого прокси.

Job ingest/collect может быть succeeded с ошибками отдельных документов/OCR: проверяйте job.result.documents/errors и parse status каждого PDF. Все готовые дубликаты дают немедленно succeeded ingest-job. sources содержит checked_at, errors и сообщения; collected document IDs уникальны. Для ссылок без поддерживаемого PDF/ZIP сохраняется ошибка источника. Ручная загрузка принимает PDF или ZIP с PDF внутри; RAR возвращает rar_unsupported.

Включение/выключение, provenance и snapshot карточки реализованы. Build-job обновляет ISS (при сбое сохраняет прежний снимок с предупреждением), разбирает выбранные PDF, извлекает и проверяет поля. Отсутствие ключа не мешает готовности карточки: доступные ISS-поля extracted/moex_iss, рисковые not_found с reason=llm_not_configured и reason_message=«LLM не настроена».

## Уточнения этапа 3

Без ключа/без документов не вызывается модель. При настроенной модели, но отсутствии выбранных PDF, доступны прямые ISS-поля и предупреждение iss_only_no_documents; отсутствие ISS-значения не заменяется нулём. При плохой цитате либо неподдержанном числовом параметре value/quote скрываются. Подтверждённая цитата с низкой уверенностью остаётся needs_review. Качество смыслового извлечения ещё требует эксперимента этапа 5.

После фиксации входов повтор воркера пропускает сохранённые результаты полей; сетевой вызов, прерванный до записи результата, может повториться, его резерв не теряется. Failed job переводит карточку в failed; экспорт/правки до ready возвращают 409. Journal хранит документы/страницы/OCR, модели, токены, known_cost_usd/cost_usd, unknown_cost_calls, время текущего исполнения и ошибки. Стоимость относится к вызовам данной build-job; предыдущая отдельная ingest-job не включается второй раз.

Admin llm-calls дополнительно возвращает state/reserved_usd/cost_source/provider_response_id; ключи и промпты не выдаются. Неизвестная стоимость сохраняет резерв и не считается нулём. Бюджетная блокировка до транспорта не является модельным вызовом: причина есть у поля, а новая строка llm_calls не создаётся.

## PDF-экспорт и пользовательские ZIP

`GET /api/cards/{id}/export.pdf` возвращает `application/pdf` и `Content-Disposition: attachment; filename="card-<ISIN>-v<версия>.pdf"`. Для неготовой карточки — 409, неизвестной — 404. Генерация использует сохранённый snapshot и правки, не вызывает ISS, LLM или внешние ресурсы. Дата формирования — UTC. Полный график остаётся в карточке/Markdown, PDF содержит первые три и ближайшие пять выплат без дублей, число известных выплат и итог из источника. Дата в прошлом не доказывает факт оплаты.

Multipart upload принимает `files` с PDF и ZIP, до пяти внешних файлов. `document_types` относится к внешним файлам по порядку: все PDF из одного ZIP наследуют его тип. ZIP проверяется общей функцией `archive_pdfs`, без записи путей архива на диск. До любых мутаций проверяется весь пакет. Не-PDF пропускаются после проверки безопасности всех записей; архив без PDF отклоняется. На пакет после распаковки — максимум `MAX_ARCHIVE_ENTRIES` PDF и `MAX_ARCHIVE_UNPACKED_BYTES`; отдельный PDF — `MAX_UPLOAD_BYTES`. Для ZIP также действуют ограничения числа всех записей, коэффициента сжатия, вложенных архивов, traversal, symlink, шифрования и целостности. Ошибки — 413 для размера тела/внешнего ZIP/PDF и совокупного лимита пакета, 422 для внутренних лимитов распаковки и небезопасных записей, 415 для неподдерживаемого/повреждённого архива. Origins: `source=upload`, `archive={url:null, filename:<имя ZIP>, entry_name:<путь PDF внутри>}`; дедупликация PDF по sha256 сохранена.
