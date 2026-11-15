import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, errorMessage } from "./api";
import {
  ErrorNotice,
  ExternalLink,
  IssueHeader,
  JobProgress,
  StatusBadge,
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

export function FieldCard({
  field,
  cardId,
  documents,
  onChange,
}: {
  field: CardField;
  cardId: string;
  documents: DocumentInfo[];
  onChange: (field: CardField) => void;
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
  const document = documents.find((d) => d.id === field.document_id);
  return (
    <article
      className={`panel field-card ${field.status === "mismatch_iss" ? "has-mismatch" : ""}`}
      aria-labelledby={"field-" + field.code}
    >
      <div className="section-heading">
        <h3 id={"field-" + field.code}>{title}</h3>
        <StatusBadge status={field.status} />
      </div>
      {field.reason_message && (
        <p className="field-reason">{field.reason_message}</p>
      )}
      {field.status === "mismatch_iss" ? (
        <div className="comparison">
          <section>
            <h4>В документе</h4>
            <ValueView value={field.value} />
          </section>
          <section>
            <h4>В MOEX ISS</h4>
            <ValueView value={field.iss_value} />
          </section>
        </div>
      ) : (
        <ValueView value={field.value} />
      )}
      {field.source.kind === "moex_iss" ? (
        <p className="source-line">
          <ExternalLink url={field.source.url}>Источник: MOEX ISS</ExternalLink>{" "}
          · Без сверки с документом
        </p>
      ) : (
        field.document_id && (
          <p className="source-line">
            <ExternalLink url={field.source.url}>
              {field.source.name || document?.filename || "Документ"}
            </ExternalLink>
            {field.page ? `, стр. ${field.page}` : ""}
            {field.clause ? `, п. ${field.clause}` : ""}
          </p>
        )
      )}
      {(field.quote || field.evidence.length > 0) && (
        <details className="quotes">
          <summary>
            Цитаты и источники
            {field.evidence.length ? ` · ${field.evidence.length + 1}` : ""}
          </summary>
          {field.quote && <blockquote>{field.quote}</blockquote>}
          {field.evidence.map((e, i) => (
            <div key={i}>
              <p className="caption">
                {documents.find((d) => d.id === e.document_id)?.filename ||
                  "Документ"}
                , стр. {e.page}
                {e.clause ? `, п. ${e.clause}` : ""}
                {e.value_path
                  ? ` · элемент ${Number(e.value_path.split("/").at(-1)) + 1}`
                  : ""}
              </p>
              <blockquote>{e.quote}</blockquote>
            </div>
          ))}
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
            Введите проверенные данные. Они сохранятся как правка пользователя.
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
    </article>
  );
}

function Journal({ card }: { card: Card }) {
  const j = card.journal;
  return (
    <details className="panel journal">
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
      {card.documents.map((d) => (
        <div className="journal-document" key={d.id}>
          <strong>{d.filename}</strong>
          <p className="caption">
            {d.enabled && d.status === "ready"
              ? "Использован"
              : "Не использован"}{" "}
            · {d.pages} стр. · OCR {d.ocr_pages} ·{" "}
            <StatusBadge status={d.status} />
          </p>
          {d.origins.map((origin, i) => (
            <p className="caption" key={i}>
              <ExternalLink url={origin.download_url || origin.source_url}>
                {sourceName(origin.source)}
              </ExternalLink>{" "}
              · опубликовано:{" "}
              {origin.published_at
                ? formatDate(origin.published_at)
                : "дата неизвестна"}{" "}
              · получено {formatDate(origin.fetched_at)}
            </p>
          ))}
        </div>
      ))}
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
  async function download() {
    setExporting(true);
    setError(null);
    try {
      const blob = await api.markdown(id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `bond-${card?.isin ?? id}-v${card?.version ?? 1}.md`;
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
          <p className="eyebrow">КАРТОЧКА ВЫПУСКА</p>
          <h1>{card ? card.isin : "Карточка облигации"}</h1>
          <p className="muted">
            {card
              ? `Версия ${card.version} · ${formatDate(card.created_at)}`
              : "Загрузка сохранённой карточки…"}
          </p>
        </div>
        <div className="actions">
          {card && (
            <a className="button secondary" href={"#/issue/" + card.isin}>
              К документам
            </a>
          )}
          {card?.status === "ready" && (
            <button disabled={exporting} onClick={() => void download()}>
              {exporting ? "Скачивание…" : "↓ Экспорт Markdown"}
            </button>
          )}
        </div>
      </div>
      <ErrorNotice message={error} retry={() => setRevision((n) => n + 1)} />
      {card && (
        <>
          <IssueHeader isin={card.isin} header={card.header} />
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
              новую версию.<a href={"#/issue/" + card.isin}>Открыть выпуск →</a>
            </div>
          )}
          {card.status === "ready" && (
            <>
              <p className="notice neutral">
                Машинный статус и проверка человеком показаны отдельно. При
                расхождении сравните документ с данными биржи.
              </p>
              {(["iss_fields", "risk_fields"] as const).map((group, index) => (
                <section className="field-group" key={group}>
                  <div className="group-heading">
                    <span className="section-number">0{index + 1}</span>
                    <div>
                      <h2>
                        {group === "iss_fields"
                          ? "Параметры выпуска — сверка с MOEX ISS"
                          : "Риски и условия — из документов"}
                      </h2>
                      <p className="caption">
                        {group === "iss_fields"
                          ? "8 полей · расхождения требуют проверки"
                          : "7 полей · отсутствие ответа не доказывает отсутствие условия"}
                      </p>
                    </div>
                  </div>
                  {Object.keys(fieldTitles)
                    .filter((code) => code in card[group])
                    .map((code) => (
                      <FieldCard
                        key={code}
                        field={card[group][code as CardField["code"]]!}
                        cardId={id}
                        documents={card.documents}
                        onChange={change}
                      />
                    ))}
                </section>
              ))}
            </>
          )}
          <Journal card={card} />
        </>
      )}
    </>
  );
}
