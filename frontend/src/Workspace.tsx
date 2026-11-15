import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, uploadDocuments } from "./api";
import {
  ErrorNotice,
  ExternalLink,
  IsinForm,
  IssueHeader,
  JobProgress,
  StatusBadge,
} from "./components";
import { formatDate, sourceName, warningText } from "./fields";
import type {
  Assisted,
  DocumentInfo,
  DocumentList,
  Issue,
  Job,
  Source,
} from "./types";

export function validateFiles(files: File[]): string | null {
  if (files.length < 1 || files.length > 5)
    return "Выберите от 1 до 5 PDF за один раз.";
  if (
    files.some(
      (file) =>
        !/\.pdf$/i.test(file.name) ||
        (file.type && file.type !== "application/pdf"),
    )
  )
    return "Можно загрузить только PDF. Архивы и другие форматы не поддерживаются.";
  if (files.some((file) => file.size === 0 || file.size > 30 * 1024 * 1024))
    return "Каждый PDF должен быть непустым и не больше 30 МиБ.";
  return null;
}
function readPending(isin: string) {
  try {
    return sessionStorage.getItem("ingest:" + isin);
  } catch {
    return null;
  }
}
function writePending(isin: string, id: string | null) {
  try {
    if (id) sessionStorage.setItem("ingest:" + isin, id);
    else sessionStorage.removeItem("ingest:" + isin);
  } catch {
    /* Storage is optional; server jobs keep running. */
  }
}
const loadWorkspace = (isin: string, signal?: AbortSignal) =>
  Promise.allSettled([
    api.issue(isin, signal),
    api.documents(isin, 0, signal),
  ] as const);

function SourceList({
  sources,
  running,
}: {
  sources: Source[];
  running: boolean;
}) {
  return (
    <div className="source-grid">
      {["prime", "skrin", "edisclosure"].map((code) => {
        const source = sources.find((item) => item.source === code);
        return (
          <section className="source-status" key={code}>
            <div className="section-heading">
              <strong>{sourceName(code)}</strong>
              {source ? (
                <StatusBadge status={source.status} />
              ) : (
                <span className="caption">
                  {running ? "Ожидает проверки" : "Ещё не проверен"}
                </span>
              )}
            </div>
            {source && (
              <>
                <p>
                  Найдено: <strong>{source.documents_found}</strong>
                </p>
                {source.message && <p className="caption">{source.message}</p>}
                {source.checked_at && (
                  <p className="caption">{formatDate(source.checked_at)}</p>
                )}
                {source.errors?.map((e, i) => (
                  <p key={i} className="error-text caption">
                    {e.message}
                  </p>
                ))}
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}
function AssistedBlock({
  assisted,
  inn,
}: {
  assisted: Assisted;
  inn?: string | null;
}) {
  const [copy, setCopy] = useState("");
  const query = inn || assisted.query;
  async function copyQuery() {
    try {
      await navigator.clipboard.writeText(query);
      setCopy("Скопировано");
    } catch {
      setCopy("Не удалось скопировать. Выделите и скопируйте текст вручную.");
    }
  }
  return (
    <aside className="assisted">
      <div className="section-heading">
        <h3>Документы e-disclosure вручную</h3>
        <span className="caption">Ассистированный режим</span>
      </div>
      <p>Капча не обходится — скачайте PDF и загрузите его ниже.</p>
      <div className="actions">
        <ExternalLink url={assisted.search_url}>
          Открыть поиск e-disclosure
        </ExternalLink>
        <span className="copy-value">
          {inn ? "ИНН" : "Поисковый запрос"}: <code>{query}</code>
        </span>
        <button
          type="button"
          className="small secondary"
          onClick={() => void copyQuery()}
        >
          Скопировать {inn ? "ИНН" : "запрос"}
        </button>
      </div>
      <p className="caption">
        Вставьте запрос в форму поиска и проверьте эмитента и выпуск перед
        скачиванием.
      </p>
      <p className="caption" role="status">
        {copy}
      </p>
    </aside>
  );
}
export function Workspace({ isin }: { isin: string }) {
  const [issue, setIssue] = useState<Issue | null>(null);
  const [listing, setListing] = useState<DocumentList | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [issError, setIssError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(() => readPending(isin));
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<string[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [drag, setDrag] = useState(false);
  const lifetime = useRef<AbortController | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const applyResults = useCallback(
    (results: Awaited<ReturnType<typeof loadWorkspace>>) => {
      const [info, docs] = results;
      if (info.status === "fulfilled") {
        setIssue(info.value);
        setIssError(null);
      } else
        setIssError(
          errorMessage(info.reason) + " Ручная загрузка PDF остаётся доступна.",
        );
      if (docs.status === "fulfilled") {
        setListing(docs.value);
        setSources(docs.value.sources);
        if (docs.value.active_collect_job_id)
          setJobId(docs.value.active_collect_job_id);
      } else if (!(
        docs.reason &&
        typeof docs.reason === "object" &&
        "status" in docs.reason &&
        docs.reason.status === 404
      ))
        setError(errorMessage(docs.reason));
    },
    [],
  );
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const results = await loadWorkspace(isin, signal);
      if (!signal?.aborted) applyResults(results);
    },
    [isin, applyResults],
  );
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    loadWorkspace(isin, controller.signal).then((results) => {
      if (!controller.signal.aborted) applyResults(results);
    });
    return () => controller.abort();
  }, [isin, applyResults]);
  const completed = useCallback(
    (job: Job) => {
      writePending(isin, null);
      setJobId(null);
      if (job.status === "failed")
        setError(job.error?.message ?? "Обработка завершилась с ошибкой.");
      void refresh(lifetime.current?.signal);
    },
    [isin, refresh],
  );
  const update = useCallback((job: Job) => {
    if (job.result?.sources) setSources(job.result.sources);
  }, []);
  async function collect() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.collect(isin);
      setSources([]);
      setJobId(result.job_id);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }
  async function build() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.build(isin);
      window.location.hash = "/cards/" + result.card_id;
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }
  async function select(doc: DocumentInfo) {
    setPending((old) => [...old, doc.id]);
    setError(null);
    try {
      const updated = await api.selectDocument(isin, doc.id, !doc.enabled);
      setListing((old) =>
        old
          ? {
              ...old,
              items: old.items.map((item) =>
                item.id === doc.id ? updated : item,
              ),
            }
          : old,
      );
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setPending((old) => old.filter((id) => id !== doc.id));
    }
  }
  function choose(next: File[]) {
    const validation = validateFiles(next);
    setFileError(validation);
    setFiles(validation ? [] : next);
  }
  async function upload() {
    const validation = validateFiles(files);
    if (validation) {
      setFileError(validation);
      return;
    }
    setBusy(true);
    setError(null);
    setUploadPercent(0);
    try {
      const result = await uploadDocuments(
        isin,
        files,
        setUploadPercent,
        lifetime.current?.signal,
      );
      writePending(isin, result.job_id);
      setJobId(result.job_id);
      setFiles([]);
      if (fileInput.current) fileInput.current.value = "";
    } catch (reason) {
      if (!lifetime.current?.signal.aborted) setError(errorMessage(reason));
    } finally {
      setBusy(false);
      setUploadPercent(null);
    }
  }
  async function moreDocuments() {
    if (!listing) return;
    setBusy(true);
    try {
      const next = await api.documents(isin, listing.items.length);
      setListing((old) =>
        old ? { ...next, items: [...old.items, ...next.items] } : next,
      );
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }
  const locked = busy || !!jobId;
  const assisted: Assisted = listing?.assisted ?? {
    source: "edisclosure",
    search_url: "https://www.e-disclosure.ru/poisk-po-kompaniyam",
    query: isin,
    mode: "manual_query",
    message: "",
  };
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">ПОДГОТОВКА КАРТОЧКИ</p>
          <h1>Документы выпуска</h1>
          <p className="muted">
            Проверьте исходные данные, выберите документы и запустите сборку.
          </p>
        </div>
        <a className="button secondary" href={"#/history?isin=" + isin}>
          История выпуска
        </a>
      </div>
      <IsinForm
        initial={isin}
        onSubmit={(value) => {
          window.location.hash = "/issue/" + value;
        }}
      />
      <ErrorNotice
        message={issError}
        retry={() => void refresh(lifetime.current?.signal)}
      />
      {issue ? (
        <>
          <IssueHeader
            isin={isin}
            header={issue.header}
            fields={Object.fromEntries(
              Object.entries(issue.normalized?.fields ?? {}).map(
                ([key, value]) => [
                  key,
                  key === "coupon_rate" && value
                    ? {
                        periods: Array.isArray(value.periods)
                          ? value.periods.slice(0, 1)
                          : [],
                        formula: value.formula ?? null,
                      }
                    : value,
                ],
              ),
            )}
            fetchedAt={issue.iss.fetched_at}
          />
          {issue.iss.status !== "available" && (
            <p className="notice warn">
              Данные ISS{" "}
              {issue.iss.status === "stale"
                ? "могут быть устаревшими"
                : "недоступны"}
              . Сборка попробует обновить снимок.
            </p>
          )}
          {!!issue.warnings.length && (
            <details className="warnings">
              <summary>
                Замечания к данным ISS · {issue.warnings.length}
              </summary>
              <ul>
                {issue.warnings.map((w, i) => (
                  <li key={i}>{warningText(w)}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      ) : (
        <p className="caption">
          {isin} · Шапка выпуска будет доступна после ответа ISS.
        </p>
      )}
      <section className="panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">ШАГ 01</p>
            <h2>Найти документы</h2>
          </div>
          <button
            className="secondary"
            onClick={() => void collect()}
            disabled={locked}
          >
            Найти документы
          </button>
        </div>
        <p className="muted">
          ПРАЙМ → СКРИН → официальный доступ e-disclosure. Частичный результат
          не мешает ручной загрузке.
        </p>
        <SourceList sources={sources} running={!!jobId} />
      </section>
      {jobId && (
        <JobProgress
          key={jobId}
          id={jobId}
          onDone={completed}
          onUpdate={update}
        />
      )}
      <section className="panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">ШАГ 02</p>
            <h2>
              Исходные документы{" "}
              <span className="count">{listing?.total ?? 0}</span>
            </h2>
          </div>
          <button
            type="button"
            className="small secondary"
            disabled={busy}
            onClick={() => void refresh(lifetime.current?.signal)}
          >
            Обновить список
          </button>
        </div>
        {!listing?.items.length ? (
          <div className="empty-state">
            <span aria-hidden="true">▤</span>
            <p>Пока нет документов</p>
            <p className="caption">Запустите поиск или загрузите PDF ниже.</p>
          </div>
        ) : (
          <div className="document-list">
            {listing.items.map((doc) => (
              <article className="document-row" key={doc.id}>
                <div className="document-main">
                  <strong>{doc.filename}</strong>
                  <div className="document-meta">
                    <StatusBadge status={doc.status} />
                    <span>
                      {doc.pages} стр. · OCR {doc.ocr_pages}
                      {doc.size_bytes != null
                        ? ` · ${(doc.size_bytes / 1024 / 1024).toFixed(1)} МиБ`
                        : ""}
                    </span>
                  </div>
                  {doc.origins.map((origin, i) => (
                    <p className="caption" key={i}>
                      <ExternalLink
                        url={origin.download_url || origin.source_url}
                      >
                        {sourceName(origin.source)}
                      </ExternalLink>{" "}
                      ·{" "}
                      {origin.published_at
                        ? formatDate(origin.published_at)
                        : "Дата публикации неизвестна"}{" "}
                      · получено {formatDate(origin.fetched_at)}
                      {origin.archive
                        ? ` · из архива: ${origin.archive.entry_name}`
                        : ""}
                    </p>
                  ))}
                  {doc.error && (
                    <p className="error-text caption">{doc.error.message}</p>
                  )}
                </div>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={doc.enabled}
                    disabled={locked || pending.includes(doc.id)}
                    onChange={() => void select(doc)}
                  />
                  Использовать <span className="sr-only">{doc.filename}</span>
                </label>
              </article>
            ))}
          </div>
        )}
        {listing && listing.items.length < listing.total && (
          <button
            className="small secondary"
            disabled={busy}
            onClick={() => void moreDocuments()}
          >
            Показать ещё документы
          </button>
        )}
        <AssistedBlock assisted={assisted} inn={issue?.header.inn} />
        <div
          className={`dropzone ${drag ? "dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            if (!locked) setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            if (!locked) choose(Array.from(e.dataTransfer.files));
          }}
        >
          <span className="upload-icon" aria-hidden="true">
            ↑
          </span>
          <h3>Загрузите свои PDF</h3>
          <p className="muted">Перетащите сюда документы или выберите файлы</p>
          <label
            className={`button secondary file-button ${locked ? "disabled" : ""}`}
          >
            Выбрать PDF
            <input
              ref={fileInput}
              type="file"
              multiple
              accept=".pdf,application/pdf"
              disabled={locked}
              onChange={(e) => choose(Array.from(e.target.files ?? []))}
            />
          </label>
          <p className="caption">
            До 5 файлов за раз · не более 30 МиБ каждый · только PDF
          </p>
        </div>
        <ErrorNotice message={fileError} />
        {!!files.length && (
          <div className="upload-selection">
            <ul>
              {files.map((file, i) => (
                <li key={i}>
                  {file.name}{" "}
                  <span className="caption">
                    {(file.size / 1024 / 1024).toFixed(2)} МиБ
                  </span>
                </li>
              ))}
            </ul>
            <button disabled={locked} onClick={() => void upload()}>
              Загрузить {files.length} PDF
            </button>
          </div>
        )}
        {uploadPercent !== null && (
          <div role="status">
            <progress
              value={uploadPercent}
              max={100}
              aria-label="Загрузка PDF"
            />
            <p className="caption">
              {uploadPercent < 100
                ? `Передано ${uploadPercent}%`
                : "Файлы переданы. Ожидаем подтверждение сервера…"}
            </p>
          </div>
        )}
      </section>
      <ErrorNotice message={error} />
      <section className="build-bar panel">
        <div>
          <p className="eyebrow">ШАГ 03</p>
          <h2>Собрать карточку</h2>
          <p className="caption">
            Выбрано {listing?.items.filter((d) => d.enabled).length ?? 0} из
            загруженных в список документов
            {listing && listing.total > listing.items.length
              ? `. Всего документов ${listing.total}: проверьте остальные страницы списка.`
              : "."}{" "}
            Новая сборка создаёт отдельную версию.
          </p>
          {!listing?.total && (
            <p className="caption">
              Если документов ещё не было, задача сначала выполнит автосбор.
            </p>
          )}
        </div>
        <button
          disabled={locked || pending.length > 0}
          onClick={() => void build()}
        >
          {busy ? "Подождите…" : "Собрать карточку →"}
        </button>
      </section>
    </>
  );
}
