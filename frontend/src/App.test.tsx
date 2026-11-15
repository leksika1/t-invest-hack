import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import * as client from "./api";
import {
  card,
  cardId,
  document,
  isin,
  issue,
  job,
  jobId,
  listing,
} from "./test/fixtures";

beforeEach(() => {
  window.location.hash = "/";
  sessionStorage.clear();
  vi.stubGlobal("scrollTo", vi.fn());
});
function mockApi(
  options: { processing?: boolean; unavailable?: boolean } = {},
) {
  let selected = true;
  let processing = !!options.processing;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    const url = path.split("?")[0];
    let result: unknown;
    if (url === "/api/health")
      result = {
        status: "ok",
        db: "ok",
        llm: "not_configured",
        version: "0.1",
      };
    else if (url === "/api/issues/" + isin && options.unavailable)
      return new Response(JSON.stringify({ detail: "ISS недоступен" }), {
        status: 502,
      });
    else if (url === "/api/issues/" + isin) result = issue;
    else if (url === "/api/issues/" + isin + "/documents")
      result = { ...listing, items: [{ ...document, enabled: selected }] };
    else if (url === `/api/issues/${isin}/documents/${document.id}`) {
      selected = JSON.parse(String(init?.body)).enabled;
      result = { ...document, enabled: selected };
    } else if (url === "/api/issues/" + isin + "/collect")
      result = { job_id: jobId };
    else if (url === "/api/cards" && init?.method === "POST") {
      processing = true;
      result = { card_id: cardId, job_id: jobId };
    } else if (url === "/api/cards")
      result = { items: [card], total: 1, limit: 20, offset: 0 };
    else if (url === "/api/cards/" + cardId) {
      result = { ...card, status: processing ? "processing" : "ready" };
    } else if (url === "/api/jobs/" + jobId) {
      processing = false;
      result = { ...job, result: { sources: listing.sources } };
    } else throw new Error("Unexpected request: " + path);
    return new Response(JSON.stringify(result), {
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
it("проходит демо → документы → collect → выбор → build → готовая карточка", async () => {
  const fetch = mockApi();
  const user = userEvent.setup();
  render(<App />);
  expect(
    await screen.findByText(/LLM не настроена — заполнены только данные биржи/),
  ).toBeVisible();
  await user.click(screen.getByRole("link", { name: /Балтийский лизинг/ }));
  expect(
    await screen.findByRole("heading", { name: "Балтийский лизинг · БО-П13" }),
  ).toBeVisible();
  expect(
    screen.getByText("Решение о выпуске.pdf", { selector: "strong" }),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Найти документы" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Найти документы" }),
    ).toBeEnabled(),
  );
  expect(screen.getByText("Нужен официальный доступ")).toBeVisible();
  const toggle = screen.getByRole("checkbox", { name: /Использовать/ });
  await user.click(toggle);
  await waitFor(() => expect(toggle).not.toBeChecked());
  await user.click(screen.getByRole("button", { name: "Собрать карточку" }));
  expect(
    await screen.findByRole("heading", {
      name: "Параметры выпуска — сверка с MOEX ISS",
    }),
  ).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "Риски и условия — из документов" }),
  ).toBeVisible();
  expect(
    fetch.mock.calls.filter(
      ([path, init]) => path === "/api/cards" && init?.method === "POST",
    ),
  ).toHaveLength(1);
  expect(window.location.hash).toBe("#/cards/" + cardId);
});
it("открывает processing карточку по прямой ссылке, восстанавливает после перезагрузки", async () => {
  const fetch = mockApi({ processing: true });
  window.location.hash = "/cards/" + cardId;
  const view = render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Ковенанты" }),
  ).toBeVisible();
  view.unmount();
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Ковенанты" }),
  ).toBeVisible();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(
    false,
  );
});
it("история фильтрует по ISIN и открывает сохранённую карточку", async () => {
  const fetch = mockApi();
  window.location.hash = "/history";
  const user = userEvent.setup();
  render(<App />);
  expect(await screen.findByRole("cell", { name: "2" })).toBeVisible();
  await user.type(screen.getByLabelText("ISIN выпуска"), isin);
  await user.click(screen.getByRole("button", { name: /Найти карточки/ }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/cards?limit=20&offset=0&isin=" + isin,
      expect.anything(),
    ),
  );
  await user.click(screen.getByRole("link", { name: "RU000A10ATW2" }));
  expect(
    await screen.findByRole("heading", { name: "Ковенанты" }),
  ).toBeVisible();
});
it("ISS недоступен: ручная загрузка доступна и передаёт PDF без автосбора", async () => {
  const fetch = mockApi({ unavailable: true });
  const upload = vi
    .spyOn(client, "uploadDocuments")
    .mockImplementation(async (_isin, _files, progress) => {
      progress(100);
      return { job_id: jobId };
    });
  window.location.hash = "/issue/" + isin;
  const user = userEvent.setup();
  render(<App />);
  expect(
    await screen.findByText(/ISS недоступен.*Ручная загрузка/),
  ).toBeVisible();
  await user.upload(
    screen.getByLabelText("Выбрать PDF"),
    new File(["%PDF-demo"], "manual.pdf", { type: "application/pdf" }),
  );
  await user.click(screen.getByRole("button", { name: "Загрузить 1 PDF" }));
  await waitFor(() => expect(upload).toHaveBeenCalled());
  expect(upload.mock.calls[0][0]).toBe(isin);
  expect(fetch.mock.calls.some(([path]) => path.endsWith("/collect"))).toBe(
    false,
  );
});
it("получение Markdown создаёт скачивание и обрабатывает ошибку экспорта", async () => {
  mockApi();
  window.location.hash = "/cards/" + cardId;
  const user = userEvent.setup();
  const create = vi.fn().mockReturnValue("blob:markdown");
  vi.stubGlobal(
    "URL",
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }),
  );
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  const markdown = vi
    .spyOn(client.api, "markdown")
    .mockResolvedValueOnce(new Blob(["# Карточка"], { type: "text/markdown" }))
    .mockRejectedValueOnce(new Error("Экспорт недоступен"));
  render(<App />);
  await user.click(
    await screen.findByRole("button", { name: /Скачать Markdown/ }),
  );
  expect(create).toHaveBeenCalled();
  expect(click).toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: /Скачать Markdown/ }));
  expect(await screen.findByText("Экспорт недоступен")).toBeVisible();
  expect(markdown).toHaveBeenCalledTimes(2);
});
it("смена маршрута отменяет polling старой задачи", async () => {
  mockApi();
  window.location.hash = "/cards/" + cardId;
  const pending = vi
    .spyOn(client.api, "card")
    .mockResolvedValue({ ...card, status: "processing" });
  const load = vi
    .spyOn(client.api, "job")
    .mockResolvedValue({ ...job, status: "running" });
  render(<App />);
  await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  const signal = load.mock.calls[0][1];
  await act(async () => {
    window.location.hash = "/history";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  expect(signal?.aborted).toBe(true);
  pending.mockRestore();
});
