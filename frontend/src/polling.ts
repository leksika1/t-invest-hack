import { api, ApiError } from "./api";
import type { Job } from "./types";

export function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const cancel = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", cancel);
      reject(new DOMException("Отменено", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", cancel);
      resolve();
    }, ms);
    if (signal.aborted) cancel();
    else signal.addEventListener("abort", cancel, { once: true });
  });
}

export async function pollJob(
  id: string,
  onUpdate: (job: Job) => void,
  signal: AbortSignal,
  onRetry: (message: string | null) => void = () => {},
  load = api.job,
): Promise<Job> {
  let failures = 0,
    unchanged = 0,
    last = "";
  while (!signal.aborted) {
    let job: Job;
    try {
      job = await load(id, signal);
    } catch (error) {
      if (signal.aborted) throw error;
      if (
        !(error instanceof ApiError) ||
        (error.status !== 0 && error.status !== 429 && error.status < 500) ||
        ++failures > 5
      )
        throw error;
      const delay = Math.max(
        error.retryAfter ?? 0,
        Math.min(1500 * 2 ** (failures - 1), 15000),
      );
      onRetry(
        `Связь с сервером прервана. Следующая проверка через ${Math.ceil(delay / 1000)} с. Задача продолжает выполняться.`,
      );
      await pause(delay, signal);
      continue;
    }
    if (signal.aborted) throw new DOMException("Отменено", "AbortError");
    failures = 0;
    onRetry(null);
    onUpdate(job);
    if (job.status === "succeeded" || job.status === "failed") return job;
    const current = `${job.step}:${job.progress}`;
    unchanged = current === last ? unchanged + 1 : 0;
    last = current;
    await pause(Math.min(1500 * 1.3 ** unchanged, 5000), signal);
  }
  throw new DOMException("Отменено", "AbortError");
}
