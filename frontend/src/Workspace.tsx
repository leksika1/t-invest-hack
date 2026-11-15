import { PaperIllustration } from "./art/guilloche";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, uploadDocuments } from "./api";
import {
  ErrorNotice,
  ExternalLink,
  IsinForm,
  IssueHeader,
  JobProgress,
  StatusBadge,
  Skeleton,
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
    return "Выберите от 1 до 5 PDF или ZIP за один раз.";
  if (files.some((file) => !/\.(pdf|zip)$/i.test(file.name)))
    return "Можно загрузить только PDF или ZIP с PDF внутри.";
  if (files.some((file) => file.size === 0))
    return "Каждый файл должен быть непустым.";
  if (
    files.some(
      (file) => /\.pdf$/i.test(file.name) && file.size > 30 * 1024 * 1024,
    )
  )
    return "Каждый PDF должен быть не больше 30 МиБ.";
  if (
    files.some(
      (file) => /\.zip$/i.test(file.name) && file.size > 100 * 1024 * 1024,
    )
  )
    return "Каждый ZIP должен быть не больше 100 МиБ.";
  if (files.reduce((total, file) => total + file.size, 0) > 150 * 1024 * 1024)
    return "Общий размер загрузки не должен превышать 150 МиБ.";
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
export function AssistedBlock({
  assisted,
  inn,
  registration,
}: {
  assisted: Assisted;
  inn?: string | null;
  registration?: string | null;
}) {
  const [copy, setCopy] = useState("");
  const query = inn || assisted.query;
  async function copyText(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopy(`${label}: скопировано`);
    } catch {
      setCopy("Не удалось скопировать. Выделите текст и скопируйте вручную.");
    }
  }
  return (
    <aside className="assisted">
      <h3>Документы e-disclosure вручную</h3>
      <ol className="assisted-steps">
        <li>
          <ExternalLink url={assisted.search_url}>
            Откройте поиск e-disclosure
          </ExternalLink>{" "}
          и найдите эмитента по {inn ? "ИНН" : "запросу"}: <code>{query}</code>{" "}
          <button
            className="small secondary"
            onClick={() => void copyText(query, "ИНН / запрос")}
          >
            Скопировать {inn ? "ИНН" : "запрос"}
          </button>
        </li>
        <li>Откройте «Документация», затем «Эмиссионные документы».</li>
        <li>
          Найдите «Решение о выпуске» и «Условия размещения» с регистрационным
          номером{" "}
          {registration ? (
            <>
              <code>{registration}</code>{" "}
              <button
                className="small secondary"
                onClick={() =>
                  void copyText(registration, "Регистрационный номер")
                }
              >
                Скопировать номер
              </button>
            </>
          ) : (
            "выпуска из ISS (пока недоступен)"
          )}
          . При наличии добавьте «Программу облигаций», сверив номер программы в
          условиях выпуска.
        </li>
        <li>
          Скачайте ZIP и перетащите сюда как есть. Проверьте эмитента и номер
          выпуска перед загрузкой.
        </li>
      </ol>
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
      <div className="workspace-layout">
        <aside className="issue-sidebar">
          {issue ? (
            <>
              <IssueHeader
                isin={isin}
                header={issue.header}
                fields={issue.normalized?.fields}
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
                    Замечания к данным ISS; {issue.warnings.length}
                  </summary>
                  <ul>
                    {issue.warnings.map((w, i) => (
                      <li key={i}>{warningText(w)}</li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          ) : issError ? (
            <p className="caption">
              Шапка выпуска недоступна. Ручная загрузка остаётся доступна.
            </p>
          ) : (
            <Skeleton label="Загружаем параметры выпуска" />
          )}
        </aside>
        <div className="workspace-process">
          <section className="panel process-step" data-step="1">
            <div className="section-heading">
              <div>
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
              Поиск на ПРАЙМ, СКРИН и проверка доступа e-disclosure. Частичный
              результат не мешает ручной загрузке.
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
          <section className="panel process-step" data-step="2">
            <div className="section-heading">
              <div>
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
            {!listing ? (
              error ? (
                <p className="caption">
                  Список документов недоступен. Нажмите «Обновить список».
                </p>
              ) : (
                <Skeleton />
              )
            ) : !listing.items.length ? (
              <div className="empty-state">
                <PaperIllustration />
                <p>Пока нет документов</p>
                <p className="caption">
                  Запустите поиск или загрузите PDF или ZIP ниже.
                </p>
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
                          {doc.pages} стр.; OCR {doc.ocr_pages}
                          {doc.size_bytes != null
                            ? `; ${(doc.size_bytes / 1024 / 1024).toFixed(1)} МиБ`
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
                          ;{" "}
                          {origin.published_at
                            ? formatDate(origin.published_at)
                            : "Дата публикации неизвестна"}{" "}
                          ; получено {formatDate(origin.fetched_at)}
                          {origin.archive
                            ? `; из архива: ${origin.archive.entry_name}`
                            : ""}
                        </p>
                      ))}
                      {doc.error && (
                        <p className="error-text caption">
                          {doc.error.message}
                        </p>
                      )}
                    </div>
                    <label className="toggle">
                      <input
                        type="checkbox"
                        checked={doc.enabled}
                        disabled={locked || pending.includes(doc.id)}
                        onChange={() => void select(doc)}
                      />
                      Использовать{" "}
                      <span className="sr-only">{doc.filename}</span>
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
            <AssistedBlock
              assisted={assisted}
              inn={issue?.header.inn}
              registration={issue?.header.registration_number}
            />
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
              <PaperIllustration kind="upload" />
              <h3>PDF или ZIP с e-disclosure — можно загружать как есть</h3>
              <p className="muted">
                Перетащите сюда документы или выберите файлы
              </p>
              <label
                className={`button secondary file-button ${locked ? "disabled" : ""}`}
              >
                Выбрать PDF или ZIP
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  accept=".pdf,.zip,application/pdf,application/zip,application/x-zip-compressed"
                  disabled={locked}
                  onChange={(e) => choose(Array.from(e.target.files ?? []))}
                />
              </label>
              <p className="caption">
                До 5 файлов: PDF до 30 МиБ, ZIP до 100 МиБ; всего до 150 МиБ. Из
                архивов извлекаются только PDF (до 100 файлов и 150 МиБ).
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
                  Загрузить файлы ({files.length})
                </button>
              </div>
            )}
            {uploadPercent !== null && (
              <div role="status">
                <progress
                  value={uploadPercent}
                  max={100}
                  aria-label="Загрузка документов"
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
          <section className="build-bar panel process-step" data-step="3">
            <div>
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
              {busy ? "Подождите…" : "Собрать карточку"}
            </button>
          </section>
        </div>
      </div>
    </>
  );
}
