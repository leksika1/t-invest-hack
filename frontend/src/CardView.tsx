import { CardSummary, SectionLink } from "./CardSummary";
import {
  displayValue,
  quoteParts,
  sourceUrl,
  shortValue,
} from "./cardPresentation";
import { CouponRibbon } from "./CouponRibbon";
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, errorMessage } from "./api";
import {
  CouponBrief,
  ErrorNotice,
  ExternalLink,
  IssueHeader,
  JobProgress,
  StatusBadge,
  Skeleton,
  ValueEditor,
  ValueView,
} from "./components";
import {
  editors,
  fieldTitles,
  formatDate,
  initialValue,
  isObject,
  sourceName,
  warningText,
} from "./fields";
import type { Card, CardField, DocumentInfo, Json, Review } from "./types";

function FieldValue({
  field,
  variableCoupon = false,
}: {
  field: CardField;
  variableCoupon?: boolean;
}) {
  if (
    field.code === "offers" &&
    Array.isArray(field.value?.events) &&
    !field.value.events.length
  )
    return <span>Оферт нет по данным MOEX ISS</span>;
  if (field.code === "coupon_rate" && field.value)
    return (
      <>
        <CouponBrief
          value={
            field.source.kind === "moex_iss"
              ? (field.iss_value ?? field.value)
              : field.value
          }
        />
        {variableCoupon && !field.value.formula && (
          <p className="caption">
            Будущие выплаты определяются по формуле (см. документы). ISS не
            подтверждает формулу.
          </p>
        )}
        <details>
          <summary>Известные купоны и параметры ставки</summary>
          <ValueView value={field.value} />
        </details>
      </>
    );
  if (field.code === "coupon_schedule") {
    const graph = displayValue(field);
    const payments = graph?.payments;
    return Array.isArray(payments) && payments.length ? (
      <>
        <p>
          {payments.length} купонных выплат
          {graph?.frequency_per_year
            ? `, ${graph.frequency_per_year} в год`
            : ""}
        </p>
        <ValueView value={payments} />
        {graph === field.iss_value && (
          <p className="caption">
            График MOEX ISS; даты не подтверждены документом.
          </p>
        )}
      </>
    ) : (
      <p className="caption">График не найден. Загрузите условия выпуска.</p>
    );
  }
  const rows = field.code === "amortization" ? field.value?.events : null;
  if (Array.isArray(rows) && rows.length > 6)
    return (
      <>
        <p>
          {rows.length} платежей амортизации
          {field.value?.frequency_per_year
            ? `, ${field.value.frequency_per_year} в год`
            : ""}
        </p>
        <ValueView value={rows} />
      </>
    );
  return <ValueView value={field.value} />;
}

export function FieldCard({
  field,
  cardId,
  documents,
  onChange,
  variableCoupon = false,
  compact = false,
}: {
  field: CardField;
  cardId: string;
  documents: DocumentInfo[];
  onChange: (field: CardField) => void;
  variableCoupon?: boolean;
  compact?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Json>(
    field.user_value ?? field.value ?? initialValue(editors[field.code]),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const title = fieldTitles[field.code];
  async function save(review: Review) {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.review(cardId, field.code, review));
      setEditing(false);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }
  function correct(e: FormEvent) {
    e.preventDefault();
    if (isObject(draft))
      void save({ user_status: "corrected", user_value: draft });
  }
  const risk = ![
    "face_value",
    "placement_date",
    "maturity_date",
    "coupon_type",
    "coupon_rate",
    "coupon_schedule",
    "offers",
    "amortization",
  ].includes(field.code);
  const parts = field.quote ? quoteParts(field.quote, field.value) : null;
  const document = documents.find((d) => d.id === field.document_id);
  return (
    <div
      className={`field-row ${risk ? "risk-detail" : ""} status-${field.status} ${field.status === "mismatch_iss" ? "has-mismatch" : ""}`}
      aria-labelledby={"field-" + field.code}
    >
      <dt className="field-title">
        <h3 tabIndex={-1} id={"field-" + field.code}>
          {title}
        </h3>
      </dt>
      <dd className="field-content">
        <div className="field-status">
          <StatusBadge
            status={
              field.source.kind === "moex_iss" &&
              field.reason === "iss_not_confirmed"
                ? "iss_unconfirmed"
                : field.status
            }
          />
        </div>
        {field.reason_message && (
          <p className="field-reason">{field.reason_message}</p>
        )}
        {risk && ["not_found", "needs_review"].includes(field.status) && (
          <p className="caption risk-next-step">
            {field.status === "not_found"
              ? "Проверьте полноту документов: решение о выпуске, условия размещения и программу."
              : "Откройте документы и проверьте условие вручную. Подтвердите найденное значение или внесите исправление."}
          </p>
        )}
        {!risk && field.status === "mismatch_iss" ? (
          <div className="comparison">
            <section>
              <h4>В документе</h4>
              <FieldValue field={field} variableCoupon={variableCoupon} />
            </section>
            <section>
              <h4>В MOEX ISS</h4>
              <ValueView value={field.iss_value} />
            </section>
          </div>
        ) : (
          <>
            {(!risk || (!field.quote && field.value)) && (
              <FieldValue field={field} variableCoupon={variableCoupon} />
            )}
            {(field.code !== "coupon_schedule" ||
              displayValue(field) !== field.iss_value) &&
              field.iss_value != null &&
              field.source.kind !== "moex_iss" && (
                <section className="iss-reference">
                  <h4>Данные MOEX ISS</h4>
                  <ValueView value={field.iss_value} />
                </section>
              )}
          </>
        )}
        {!compact && field.source.kind === "moex_iss" ? (
          <p className="source-line">
            <ExternalLink url={field.source.url}>
              Источник: MOEX ISS
            </ExternalLink>
            <span> · Без сверки с документом</span>
          </p>
        ) : (
          field.document_id && (
            <p className="source-line">
              <ExternalLink
                url={sourceUrl(
                  field.source.url ||
                    document?.origins.find((o) => o.download_url)?.download_url,
                  field.page,
                )}
              >
                {field.source.name || document?.filename || "Документ"}
                {field.page ? `, стр. ${field.page}` : ""}
                {field.clause ? `, п. ${field.clause}` : ""}
              </ExternalLink>
            </p>
          )
        )}
        {(field.quote || field.evidence.length > 0) && (
          <details className="quotes" open={risk || undefined}>
            <summary>
              Цитаты и источники
              {field.evidence.length ? `; ${field.evidence.length + 1}` : ""}
            </summary>
            {parts && (
              <blockquote>
                {parts[0]}
                <mark>{parts[1]}</mark>
                {parts[2]}
              </blockquote>
            )}
            {field.evidence.map((e, i) => (
              <div key={i}>
                <p className="caption">
                  <ExternalLink
                    url={sourceUrl(
                      documents
                        .find((d) => d.id === e.document_id)
                        ?.origins.find((o) => o.download_url)?.download_url,
                      e.page,
                    )}
                  >
                    {documents.find((d) => d.id === e.document_id)?.filename ||
                      "Документ"}
                    , стр. {e.page}
                    {e.clause ? `, п. ${e.clause}` : ""}
                  </ExternalLink>
                  {e.value_path
                    ? `; элемент ${Number(e.value_path.split("/").at(-1)) + 1}`
                    : ""}
                </p>
                <blockquote>{e.quote}</blockquote>
              </div>
            ))}
          </details>
        )}
        {risk && field.quote && field.value && (
          <details className="risk-value">
            <summary>Распознанное условие полностью</summary>
            <ValueView value={field.value} />
          </details>
        )}
        {field.user_status && (
          <section className="human-review">
            <StatusBadge status={field.user_status} />
            {field.user_status === "corrected" && (
              <>
                <h4>Значение пользователя</h4>
                <ValueView value={field.user_value} />
                <p className="caption">
                  Исходный машинный результат сохранён выше.
                </p>
              </>
            )}
            {field.reviewed_at && (
              <p className="caption">{formatDate(field.reviewed_at)}</p>
            )}
          </section>
        )}
        <div className="field-actions">
          <button
            type="button"
            className="small secondary"
            disabled={busy || field.value === null}
            onClick={() => void save({ user_status: "confirmed" })}
          >
            Подтвердить
          </button>
          <button
            type="button"
            className="small secondary"
            disabled={busy}
            onClick={() => {
              setDraft(
                field.user_value ??
                  field.value ??
                  initialValue(editors[field.code]),
              );
              setEditing(!editing);
              setError(null);
            }}
          >
            {editing ? "Закрыть форму" : "Исправить"}
          </button>
          {busy && (
            <span role="status" className="caption">
              Сохранение…
            </span>
          )}
        </div>
        {editing && (
          <form className="correction-form" onSubmit={correct}>
            <p className="caption">
              Введите проверенные данные. Они сохранятся как правка
              пользователя.
            </p>
            <fieldset disabled={busy}>
              <ValueEditor
                node={editors[field.code]}
                value={draft}
                onChange={setDraft}
                title={title}
              />
              <div className="actions">
                <button type="submit" className="small">
                  Сохранить исправление
                </button>
                <button
                  type="button"
                  className="small secondary"
                  onClick={() => setEditing(false)}
                >
                  Отмена
                </button>
              </div>
            </fieldset>
          </form>
        )}
        <ErrorNotice message={error} />
      </dd>
    </div>
  );
}

function Journal({ card }: { card: Card }) {
  const j = card.journal;
  return (
    <details
      id="card-journal"
      tabIndex={-1}
      className="panel journal card-section"
    >
      <summary>Журнал сборки и документы</summary>
      <dl className="metrics">
        <div>
          <dt>Документов использовано</dt>
          <dd>{j.documents_used ?? "—"}</dd>
        </div>
        <div>
          <dt>Страниц / через OCR</dt>
          <dd>
            {j.pages ?? "—"} / {j.ocr_pages ?? "—"}
          </dd>
        </div>
        <div>
          <dt>Модель</dt>
          <dd>{j.models?.join(", ") || "Без вызовов модели"}</dd>
        </div>
        <div>
          <dt>Стоимость, USD</dt>
          <dd>
            {j.cost_usd ?? "Неизвестна"}
            {!!j.unknown_cost_calls && (
              <small> Известная часть: {j.known_cost_usd ?? "0"}</small>
            )}
          </dd>
        </div>
        <div>
          <dt>Время исполнения</dt>
          <dd>
            {j.duration_ms != null
              ? `${(j.duration_ms / 1000).toFixed(1)} с`
              : "—"}
          </dd>
        </div>
      </dl>
      <div
        className="table-scroll"
        tabIndex={0}
        role="region"
        aria-label="Документы сборки"
      >
        <table>
          <thead>
            <tr>
              <th>Документ</th>
              <th>Использование</th>
              <th>Страницы / OCR</th>
              <th>Статус</th>
              <th>Источники</th>
            </tr>
          </thead>
          <tbody>
            {card.documents.map((d) => (
              <tr key={d.id}>
                <td>{d.filename}</td>
                <td>
                  {d.enabled && d.status === "ready"
                    ? "Использован"
                    : "Не использован"}
                </td>
                <td>
                  {d.pages} / {d.ocr_pages}
                </td>
                <td>
                  <StatusBadge status={d.status} />
                </td>
                <td>
                  {d.origins.map((origin, i) => (
                    <p key={i}>
                      <ExternalLink
                        url={origin.download_url || origin.source_url}
                      >
                        {sourceName(origin.source)}
                      </ExternalLink>
                      <br />
                      <span className="caption">
                        Получено {formatDate(origin.fetched_at)}. Публикация:{" "}
                        {formatDate(origin.published_at)}
                      </span>
                    </p>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!!j.warnings?.length && (
        <ul className="caption">
          {j.warnings.map((w, i) => (
            <li key={i}>{warningText(w)}</li>
          ))}
        </ul>
      )}
      {j.errors?.map((e, i) => (
        <p className="error-text" key={i}>
          {e.message}
        </p>
      ))}
    </details>
  );
}

export function CardPage({ id }: { id: string }) {
  const [card, setCard] = useState<Card | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [exporting, setExporting] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api
      .card(id, controller.signal)
      .then((value) => {
        setCard(value);
        setError(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [id, revision]);
  const completed = useCallback(() => {
    setRevision((value) => value + 1);
  }, []);
  const change = useCallback(
    (field: CardField) =>
      setCard((old) =>
        old
          ? {
              ...old,
              iss_fields:
                field.code in old.iss_fields
                  ? { ...old.iss_fields, [field.code]: field }
                  : old.iss_fields,
              risk_fields:
                field.code in old.risk_fields
                  ? { ...old.risk_fields, [field.code]: field }
                  : old.risk_fields,
            }
          : old,
      ),
    [],
  );
  async function download(format: "pdf" | "md") {
    setExporting(true);
    setError(null);
    try {
      const blob = await (format === "pdf" ? api.pdf(id) : api.markdown(id));
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${format === "pdf" ? "card" : "bond"}-${card?.isin ?? id}-v${card?.version ?? 1}.${format}`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setExporting(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>{card ? card.isin : "Карточка облигации"}</h1>
          <p className="muted">
            {card
              ? `Версия ${card.version}; ${formatDate(card.created_at)}`
              : "Параметры, условия и источники выпуска"}
          </p>
        </div>
        <div className="actions">
          {card?.status === "ready" && (
            <>
              <button disabled={exporting} onClick={() => void download("pdf")}>
                {exporting ? "Скачивание…" : "Скачать PDF"}
              </button>
              <button
                className="secondary"
                disabled={exporting}
                onClick={() => void download("md")}
              >
                Markdown
              </button>
            </>
          )}
          {card && (
            <a className="button secondary" href={"#/issue/" + card.isin}>
              К документам
            </a>
          )}
        </div>
      </div>
      <ErrorNotice message={error} retry={() => setRevision((n) => n + 1)} />
      {!card && !error && <Skeleton label="Загружаем карточку" />}
      {card && (
        <>
          <IssueHeader
            isin={card.isin}
            header={card.header}
            fields={
              card.status === "ready"
                ? Object.fromEntries(
                    Object.entries(card.iss_fields).map(([key, field]) => [
                      key,
                      field?.source.kind === "moex_iss"
                        ? (field.iss_value ?? field.value)
                        : field?.value,
                    ]),
                  )
                : undefined
            }
          />
          {card.status === "processing" && card.job_id && (
            <JobProgress
              key={card.job_id}
              id={card.job_id}
              onDone={completed}
            />
          )}
          {card.status === "failed" && (
            <div className="notice danger" role="alert">
              Сборка не завершена. Проверьте журнал и документы, затем создайте
              новую версию.<a href={"#/issue/" + card.isin}>Открыть выпуск</a>
            </div>
          )}
          {card.status === "ready" && (
            <>
              <nav className="card-nav" aria-label="Разделы карточки">
                {[
                  ["summary", "Сводка"],
                  ["coupons", "Купоны"],
                  ["parameters", "Параметры"],
                  ["risks", "Риски"],
                  ["journal", "Журнал"],
                ].map(([section, title]) => (
                  <SectionLink key={section} id={`card-${section}`}>
                    {title}
                  </SectionLink>
                ))}
              </nav>
              <CardSummary card={card} />
              <section id="card-coupons" className="card-section" tabIndex={-1}>
                <CouponRibbon
                  schedule={displayValue(card.iss_fields.coupon_schedule)}
                  rate={displayValue(card.iss_fields.coupon_rate)}
                />
                {displayValue(card.iss_fields.coupon_schedule) ===
                  card.iss_fields.coupon_schedule?.iss_value && (
                  <p className="caption">
                    График из MOEX ISS. Даты не подтверждены документами
                    выпуска.
                  </p>
                )}
              </section>
              <section
                id="card-parameters"
                className="card-section field-group"
                tabIndex={-1}
              >
                <h2>Параметры выпуска</h2>
                <p className="caption">
                  MOEX ISS — справочные данные биржи. Статус показывает
                  результат сверки с документами; пустые значения не считаются
                  подтверждением.
                </p>
                <div className="parameter-table">
                  {Object.values(card.iss_fields)
                    .filter((f) => f != null)
                    .map((field) => (
                      <details key={field.code} className="parameter-row">
                        <summary>
                          <strong>{fieldTitles[field.code]}</strong>
                          <span>{shortValue(displayValue(field))}</span>
                          <StatusBadge
                            status={
                              field.source.kind === "moex_iss" &&
                              field.reason === "iss_not_confirmed"
                                ? "iss_unconfirmed"
                                : field.status
                            }
                          />
                        </summary>
                        <dl>
                          <FieldCard
                            field={field}
                            cardId={id}
                            documents={card.documents}
                            onChange={change}
                            compact
                            variableCoupon={["floating", "variable"].includes(
                              String(
                                displayValue(card.iss_fields.coupon_type)?.type,
                              ),
                            )}
                          />
                        </dl>
                      </details>
                    ))}
                </div>
              </section>
              <section
                id="card-risks"
                className="card-section field-group"
                tabIndex={-1}
              >
                <h2>Рисковые условия</h2>
                <p className="caption">
                  Основа вывода — цитата из документа. Статус и ручная проверка
                  сохраняются отдельно.
                </p>
                <dl className="field-list">
                  {Object.values(card.risk_fields)
                    .filter((f) => f != null)
                    .map((field) => (
                      <FieldCard
                        key={field.code}
                        field={field}
                        cardId={id}
                        documents={card.documents}
                        onChange={change}
                      />
                    ))}
                </dl>
              </section>
            </>
          )}
          <Journal card={card} />
        </>
      )}
    </>
  );
}
