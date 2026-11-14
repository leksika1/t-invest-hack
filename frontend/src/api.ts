export type Health = {
  status: string;
  db: string;
  llm: string;
  version: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d: { msg: string }) => d.msg).join("; ")
      : detail ?? res.statusText;
    throw new Error(`${res.status}: ${message}`);
  }
  return body as T;
}

export const getHealth = () => request<Health>("/api/health");

export const buildCard = (isin: string) =>
  request<unknown>("/api/cards", { method: "POST", body: JSON.stringify({ isin }) });
