import type {
  Card,
  CardField,
  CardSummary,
  DocumentInfo,
  DocumentList,
  Health,
  Issue,
  Job,
  Page,
  Review,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
    public retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export function retryDelay(value: string | null): number | null {
  if (!value) return null;
  const seconds = Number(value);
  const ms = Number.isFinite(seconds)
    ? seconds * 1000
    : Date.parse(value) - Date.now();
  return Number.isFinite(ms) ? Math.max(0, ms) : null;
}
function errorText(body: unknown, status: number): string {
  if (body && typeof body === "object") {
    const data = body as {
      detail?: unknown;
      error?: { message?: unknown };
      message?: unknown;
    };
    const detail = data.detail ?? data.error ?? data;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
      return (
        "Проверьте введённые данные: " +
        detail
          .map((d) => (typeof d?.msg === "string" ? d.msg : "неверный формат"))
          .join("; ")
      );
    if (
      detail &&
      typeof detail === "object" &&
      "message" in detail &&
      typeof detail.message === "string"
    )
      return detail.message;
  }
  return (
    (
      {
        404: "Данные не найдены.",
        413: "Превышен допустимый размер загрузки.",
        415: "Загрузите документы в формате PDF.",
        422: "Проверьте формат введённых данных.",
        429: "Слишком много запросов.",
        502: "Источник данных временно недоступен.",
        503: "Сервис временно недоступен.",
      } as Record<number, string>
    )[status] ?? `Не удалось выполнить запрос (${status}).`
  );
}
async function checked(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch("/api" + path, init);
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new ApiError(
      "Нет соединения с сервером. Проверьте подключение и повторите действие.",
    );
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const delay = retryDelay(response.headers.get("Retry-After"));
    const suffix =
      response.status === 429 && delay !== null
        ? ` Повторите через ${Math.max(1, Math.ceil(delay / 1000))} с.`
        : "";
    throw new ApiError(
      errorText(body, response.status) + suffix,
      response.status,
      delay,
    );
  }
  return response;
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await checked(path, init);
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("Сервер вернул ответ в неподдерживаемом формате.", 502);
  }
}
const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const code = encodeURIComponent;
export const api = {
  health: (signal?: AbortSignal) => request<Health>("/health", { signal }),
  issue: (isin: string, signal?: AbortSignal) =>
    request<Issue>("/issues/" + code(isin), { signal }),
  documents: (isin: string, offset = 0, signal?: AbortSignal) =>
    request<DocumentList>(
      `/issues/${code(isin)}/documents?limit=100&offset=${offset}`,
      { signal },
    ),
  selectDocument: (isin: string, id: string, enabled: boolean) =>
    request<DocumentInfo>(
      `/issues/${code(isin)}/documents/${code(id)}`,
      json("PATCH", { enabled }),
    ),
  collect: (isin: string) =>
    request<{ job_id: string }>(`/issues/${code(isin)}/collect`, {
      method: "POST",
    }),
  build: (isin: string) =>
    request<{ card_id: string; job_id: string }>(
      "/cards",
      json("POST", { isin }),
    ),
  job: (id: string, signal?: AbortSignal) =>
    request<Job>("/jobs/" + code(id), { signal }),
  card: (id: string, signal?: AbortSignal) =>
    request<Card>("/cards/" + code(id), { signal }),
  cards: (isin = "", offset = 0, signal?: AbortSignal) =>
    request<Page<CardSummary>>(
      `/cards?limit=20&offset=${offset}${isin ? "&isin=" + code(isin) : ""}`,
      { signal },
    ),
  review: (id: string, field: string, body: Review) =>
    request<CardField>(
      `/cards/${code(id)}/fields/${code(field)}`,
      json("PATCH", body),
    ),
  markdown: async (id: string) =>
    (await checked(`/cards/${code(id)}/export.md`)).blob(),
};
// XHR exposes actual byte progress; multipart headers are supplied by the browser.
export function uploadDocuments(
  isin: string,
  files: File[],
  onProgress: (percent: number) => void,
  signal?: AbortSignal,
): Promise<{ job_id: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const cleanup = () => signal?.removeEventListener("abort", abort);
    if (signal?.aborted) {
      reject(new DOMException("Отменено", "AbortError"));
      return;
    }
    xhr.open("POST", `/api/issues/${code(isin)}/documents`);
    xhr.timeout = 180000;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable)
        onProgress(Math.round((100 * e.loaded) / e.total));
    };
    xhr.onload = () => {
      cleanup();
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (
        xhr.status >= 200 &&
        xhr.status < 300 &&
        body &&
        typeof body === "object" &&
        "job_id" in body &&
        typeof body.job_id === "string"
      )
        resolve({ job_id: body.job_id });
      else {
        const delay = retryDelay(xhr.getResponseHeader("Retry-After"));
        reject(
          new ApiError(
            errorText(body, xhr.status) +
              (xhr.status === 429 && delay !== null
                ? ` Повторите через ${Math.max(1, Math.ceil(delay / 1000))} с.`
                : ""),
            xhr.status,
            delay,
          ),
        );
      }
    };
    xhr.onerror = () => {
      cleanup();
      reject(new ApiError("Не удалось загрузить файлы. Проверьте соединение."));
    };
    xhr.ontimeout = () => {
      cleanup();
      reject(
        new ApiError(
          "Время загрузки истекло. Проверьте список документов перед повтором.",
        ),
      );
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException("Отменено", "AbortError"));
    };
    signal?.addEventListener("abort", abort, { once: true });
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    xhr.send(form);
  });
}
export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "Не удалось выполнить действие.";
export function safeUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}
