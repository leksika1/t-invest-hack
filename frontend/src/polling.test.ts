import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { pollJob } from "./polling";
import { ApiError } from "./api";
import { job, jobId } from "./test/fixtures";
beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());
it("опрашивает до терминального состояния и замедляется без прогресса", async () => {
  const running = { ...job, status: "running" as const, progress: 25 };
  const load = vi
    .fn()
    .mockResolvedValueOnce(running)
    .mockResolvedValueOnce(running)
    .mockResolvedValue(job);
  const update = vi.fn();
  const result = pollJob(
    jobId,
    update,
    new AbortController().signal,
    vi.fn(),
    load,
  );
  await vi.advanceTimersByTimeAsync(1499);
  expect(load).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(load).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1949);
  expect(load).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1);
  await expect(result).resolves.toEqual(job);
  expect(load).toHaveBeenCalledTimes(3);
  expect(update).toHaveBeenCalledTimes(3);
  await vi.advanceTimersByTimeAsync(100000);
  expect(load).toHaveBeenCalledTimes(3);
});
it("уважает Retry-After на 429 и не создаёт новую задачу", async () => {
  const load = vi
    .fn()
    .mockRejectedValueOnce(new ApiError("Лимит", 429, 6000))
    .mockResolvedValue(job);
  const retry = vi.fn();
  const result = pollJob(
    jobId,
    vi.fn(),
    new AbortController().signal,
    retry,
    load,
  );
  await vi.advanceTimersByTimeAsync(5999);
  expect(load).toHaveBeenCalledTimes(1);
  expect(retry).toHaveBeenCalledWith(expect.stringContaining("6 с"));
  await vi.advanceTimersByTimeAsync(1);
  await expect(result).resolves.toEqual(job);
});
it("отмена останавливает таймер и последующие запросы", async () => {
  const controller = new AbortController();
  const load = vi.fn().mockResolvedValue({ ...job, status: "running" });
  const result = pollJob(jobId, vi.fn(), controller.signal, vi.fn(), load);
  const rejected = expect(result).rejects.toHaveProperty("name", "AbortError");
  await vi.advanceTimersByTimeAsync(100);
  controller.abort();
  await rejected;
  await vi.advanceTimersByTimeAsync(10000);
  expect(load).toHaveBeenCalledTimes(1);
});
it("failed-job завершает polling, 404 не повторяется", async () => {
  const failed = {
    ...job,
    status: "failed" as const,
    error: { message: "Ошибка PDF" },
  };
  const load = vi.fn().mockResolvedValue(failed);
  await expect(
    pollJob(jobId, vi.fn(), new AbortController().signal, vi.fn(), load),
  ).resolves.toEqual(failed);
  expect(load).toHaveBeenCalledTimes(1);
  const missing = vi.fn().mockRejectedValue(new ApiError("Не найдено", 404));
  await expect(
    pollJob(jobId, vi.fn(), new AbortController().signal, vi.fn(), missing),
  ).rejects.toMatchObject({ status: 404 });
  expect(missing).toHaveBeenCalledTimes(1);
});
it("повторяет сетевую ошибку с backoff, но не бесконечно", async () => {
  const load = vi.fn().mockRejectedValue(new ApiError("Нет связи"));
  const result = pollJob(
    jobId,
    vi.fn(),
    new AbortController().signal,
    vi.fn(),
    load,
  );
  const rejected = expect(result).rejects.toThrow("Нет связи");
  await vi.runAllTimersAsync();
  await rejected;
  expect(load).toHaveBeenCalledTimes(6);
});
