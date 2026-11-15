import { expect, it, vi } from "vitest";
import { api, ApiError, retryDelay, uploadDocuments } from "./api";
it.each([
  { detail: { code: "budget", message: "Бюджет исчерпан" } },
  { error: { message: "Бюджет исчерпан" } },
  { detail: "Бюджет исчерпан" },
])("читает сообщение ошибки %j", async (body) => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(body), { status: 503 })),
  );
  await expect(api.build("RU000A10ATW2")).rejects.toThrow("Бюджет исчерпан");
});
it("показывает Retry-After, передаёт JSON только в JSON-запросе", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(
        JSON.stringify({ detail: { message: "Слишком много запросов" } }),
        { status: 429, headers: { "Retry-After": "12" } },
      ),
    );
  vi.stubGlobal("fetch", fetch);
  await expect(api.build("RU000A10ATW2")).rejects.toMatchObject({
    status: 429,
    retryAfter: 12000,
    message: expect.stringContaining("12 с"),
  });
  expect(fetch).toHaveBeenCalledWith(
    "/api/cards",
    expect.objectContaining({
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }),
  );
  expect(retryDelay("invalid")).toBeNull();
  expect(
    retryDelay(new Date(Date.now() + 20000).toUTCString())!,
  ).toBeGreaterThan(18000);
});
it("обрабатывает FastAPI 422 и HTML от прокси без [object Object]", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: [{ msg: "Неверный ISIN" }] }), {
        status: 422,
      }),
    )
    .mockResolvedValueOnce(
      new Response("<html>Bad gateway</html>", { status: 502 }),
    );
  vi.stubGlobal("fetch", fetch);
  await expect(api.build("x")).rejects.toThrow("Неверный ISIN");
  await expect(api.health()).rejects.toThrow(
    "Источник данных временно недоступен",
  );
});
it("отличает отмену запроса от потери сети", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
  await expect(api.health()).rejects.toBeInstanceOf(ApiError);
  const controller = new AbortController();
  controller.abort();
  await expect(api.health(controller.signal)).rejects.toBeInstanceOf(TypeError);
});
it("загрузка передаёт FormData без ручного Content-Type и отслеживает реальные байты", async () => {
  const instances: FakeXHR[] = [];
  class FakeXHR {
    upload: {
      onprogress?: (event: {
        lengthComputable: boolean;
        loaded: number;
        total: number;
      }) => void;
    } = {};
    onload?: () => void;
    onabort?: () => void;
    status = 202;
    responseText = '{"job_id":"ingest-job"}';
    timeout = 0;
    open = vi.fn();
    send = vi.fn();
    setRequestHeader = vi.fn();
    getResponseHeader = vi.fn().mockReturnValue(null);
    abort() {
      this.onabort?.();
    }
    constructor() {
      instances.push(this);
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
  const progress = vi.fn();
  const controller = new AbortController();
  const promise = uploadDocuments(
    "RU000A10ATW2",
    [new File(["pdf"], "x.pdf")],
    progress,
    controller.signal,
  );
  const xhr = instances[0];
  expect(xhr!.open).toHaveBeenCalledWith(
    "POST",
    "/api/issues/RU000A10ATW2/documents",
  );
  expect(xhr!.setRequestHeader).not.toHaveBeenCalled();
  const form = xhr!.send.mock.calls[0][0];
  expect(form).toBeInstanceOf(FormData);
  expect(form.getAll("files")).toHaveLength(1);
  xhr!.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 });
  expect(progress).toHaveBeenCalledWith(50);
  xhr!.onload?.();
  await expect(promise).resolves.toEqual({ job_id: "ingest-job" });
});

it("PDF API получает бинарный отчёт и сохраняет серверную ошибку 409", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response("%PDF-1.7", {
        headers: { "Content-Type": "application/pdf" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Карточка ещё не готова" }), {
        status: 409,
      }),
    );
  vi.stubGlobal("fetch", fetch);
  const blob = await api.pdf("report-id");
  expect(blob.type).toBe("application/pdf");
  expect(await blob.text()).toBe("%PDF-1.7");
  expect(fetch).toHaveBeenCalledWith("/api/cards/report-id/export.pdf", {});
  await expect(api.pdf("report-id")).rejects.toThrow("Карточка ещё не готова");
});
