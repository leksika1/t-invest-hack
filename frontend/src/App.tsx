import { FormEvent, useEffect, useState } from "react";
import { buildCard, getHealth, Health } from "./api";

const styles = {
  page: { fontFamily: "system-ui, sans-serif", maxWidth: 860, margin: "40px auto", padding: "0 16px" },
  status: { padding: "8px 12px", background: "#f3f4f6", borderRadius: 6, fontSize: 14 },
  form: { display: "flex", gap: 8, margin: "24px 0" },
  input: { flex: 1, padding: 8, fontSize: 16, fontFamily: "monospace" },
  button: { padding: "8px 16px", fontSize: 16, cursor: "pointer" },
  pre: { background: "#111827", color: "#e5e7eb", padding: 16, borderRadius: 6, overflow: "auto", fontSize: 13 },
  error: { color: "#b91c1c" },
} as const;

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [isin, setIsin] = useState("");
  const [card, setCard] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch((e: Error) => setHealthError(e.message));
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setCard(null);
    try {
      setCard(await buildCard(isin));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main style={styles.page}>
      <h1>Карточка облигации</h1>
      <p>
        Условия выпуска корпоративной облигации из эмиссионных документов со ссылками на источник и сверкой с
        MOEX ISS. Это факты из документов, не инвестиционная рекомендация.
      </p>

      <div style={styles.status}>
        Бэкенд:{" "}
        {health
          ? `status=${health.status}, db=${health.db}, llm=${health.llm}, version=${health.version}`
          : healthError
            ? <span style={styles.error}>недоступен ({healthError})</span>
            : "проверка…"}
      </div>

      <form style={styles.form} onSubmit={onSubmit}>
        <input
          style={styles.input}
          value={isin}
          onChange={(e) => setIsin(e.target.value)}
          placeholder="ISIN, например RU000A10AXF7"
          maxLength={12}
        />
        <button style={styles.button} type="submit" disabled={loading || !isin.trim()}>
          {loading ? "Собираю…" : "Собрать карточку"}
        </button>
      </form>

      {error && <p style={styles.error}>Ошибка: {error}</p>}
      {card !== null && <pre style={styles.pre}>{JSON.stringify(card, null, 2)}</pre>}
    </main>
  );
}
