import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { CardPage } from "./CardView";
import { ErrorNotice, IsinForm, StatusBadge } from "./components";
import { formatDate, validIsin } from "./fields";
import type { CardSummary, Health } from "./types";
import { Workspace } from "./Workspace";
import "./styles.css";

export const DISCLAIMER =
  "Факты из документов эмитента, не инвестиционная рекомендация";
function Home() {
  return (
    <>
      <div className="hero">
        <p className="eyebrow">АНАЛИЗ УСЛОВИЙ ВЫПУСКА</p>
        <h1>
          Каждое условие —<br />с опорой на источник.
        </h1>
        <p className="hero-copy">
          Параметры биржи, эмиссионные документы и рисковые условия в одной
          карточке. С цитатами и проверкой данных.
        </p>
      </div>
      <section className="panel search-panel">
        <IsinForm
          onSubmit={(isin) => {
            window.location.hash = "/issue/" + isin;
          }}
        />
        <div className="demo-options">
          <span className="caption">Демо-выпуски</span>
          <a href="#/issue/RU000A10ATW2">
            Балтийский лизинг <span>RU000A10ATW2 · ПРАЙМ ↗</span>
          </a>
          <a href="#/issue/RU000A10CS75">
            Аэрофлот <span>RU000A10CS75 · СКРИН ↗</span>
          </a>
        </div>
      </section>
      <div className="intro-grid">
        {[
          [
            "01",
            "Подготовьте документы",
            "Автосбор из раскрытия или загрузка своих PDF. Вы сами выбираете источники для сборки.",
          ],
          [
            "02",
            "Сопоставьте факты",
            "8 параметров выпуска со сверкой ISS и 7 групп рисковых условий из документов.",
          ],
          [
            "03",
            "Проверьте и сохраните",
            "Откройте цитаты, подтвердите или исправьте результат и скачайте Markdown.",
          ],
        ].map(([n, title, text]) => (
          <section key={n}>
            <span className="section-number">{n}</span>
            <h2>{title}</h2>
            <p className="muted">{text}</p>
          </section>
        ))}
      </div>
    </>
  );
}
function HistoryPage({ isin }: { isin: string }) {
  const [items, setItems] = useState<CardSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api
      .cards(isin, offset, controller.signal)
      .then((page) => {
        setItems(page.items);
        setTotal(page.total);
        setError(null);
        setLoading(false);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(reason));
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [isin, offset, revision]);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">СОХРАНЁННЫЕ РЕЗУЛЬТАТЫ</p>
          <h1>История карточек</h1>
          <p className="muted">
            Каждая сборка — отдельная версия со своими документами и правками.
          </p>
        </div>
        <a href="#/" className="button secondary">
          Новый выпуск
        </a>
      </div>
      <section className="panel">
        <IsinForm
          initial={isin}
          label="Найти карточки"
          onSubmit={(value) => {
            window.location.hash = "/history?isin=" + value;
          }}
        />
        {isin && (
          <p>
            <a href="#/history">Все выпуски</a>
          </p>
        )}
        <ErrorNotice message={error} retry={() => setRevision((n) => n + 1)} />
        {loading ? (
          <p role="status">Загрузка истории…</p>
        ) : !items.length && !error ? (
          <div className="empty-state">
            <p>Карточек пока нет</p>
            <p className="caption">
              {isin
                ? "Для этого выпуска ещё не сохранено карточек."
                : "Откройте выпуск и создайте первую карточку."}
            </p>
          </div>
        ) : (
          <div className="history-list">
            {items.map((card) => (
              <a
                href={"#/cards/" + card.id}
                className="history-row"
                key={card.id}
              >
                <div>
                  <strong>{card.isin}</strong>
                  <p className="caption">
                    Версия {card.version} · {formatDate(card.created_at)}
                  </p>
                </div>
                <StatusBadge status={card.status} />
                <span aria-hidden="true">→</span>
              </a>
            ))}
          </div>
        )}
        <div className="pagination">
          <span className="caption">
            {total
              ? `${offset + 1}–${Math.min(offset + 20, total)} из ${total}`
              : "0 карточек"}
          </span>
          <div className="actions">
            <button
              className="small secondary"
              disabled={loading || offset === 0}
              onClick={() => {
                setLoading(true);
                setOffset((n) => Math.max(0, n - 20));
              }}
            >
              Назад
            </button>
            <button
              className="small secondary"
              disabled={loading || offset + 20 >= total}
              onClick={() => {
                setLoading(true);
                setOffset((n) => n + 20);
              }}
            >
              Далее
            </button>
          </div>
        </div>
      </section>
    </>
  );
}
function routeHash() {
  return window.location.hash.slice(1) || "/";
}
export default function App() {
  const [route, setRoute] = useState(routeHash);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [healthRevision, setHealthRevision] = useState(0);
  useEffect(() => {
    const change = () => {
      setRoute(routeHash());
      document.getElementById("main-content")?.focus();
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    api
      .health(controller.signal)
      .then((value) => {
        setHealth(value);
        setError(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [healthRevision]);
  const [path, query = ""] = route.split("?");
  const parts = path.split("/").filter(Boolean);
  const isHistory = parts[0] === "history";
  const isin = new URLSearchParams(query).get("isin") ?? "";
  let page;
  if (!parts.length) page = <Home />;
  else if (parts.length === 2 && parts[0] === "issue" && validIsin(parts[1]))
    page = <Workspace key={parts[1]} isin={parts[1].toUpperCase()} />;
  else if (
    parts.length === 2 &&
    parts[0] === "cards" &&
    /^[a-zA-Z0-9-]+$/.test(parts[1])
  )
    page = <CardPage key={parts[1]} id={parts[1]} />;
  else if (isHistory && parts.length === 1 && (!isin || validIsin(isin)))
    page = <HistoryPage key={isin} isin={isin.toUpperCase()} />;
  else
    page = (
      <section className="panel">
        <h1>Страница не найдена</h1>
        <p>
          Проверьте ссылку или <a href="#/">откройте выпуск по ISIN</a>.
        </p>
      </section>
    );
  return (
    <>
      <a
        href="#main-content"
        className="skip-link"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        К содержимому
      </a>
      <header className="app-header">
        <div className="header-inner">
          <a
            className="brand"
            href="#/"
            aria-label="Карточка облигации — главная"
          >
            <span className="brand-mark" aria-hidden="true">
              ▥
            </span>
            <span>
              Карточка<span className="brand-sub">облигации</span>
            </span>
          </a>
          <nav aria-label="Основная навигация">
            <a href="#/" aria-current={!isHistory ? "page" : undefined}>
              Рабочее пространство
            </a>
            <a href="#/history" aria-current={isHistory ? "page" : undefined}>
              История
            </a>
          </nav>
          <span className="service-status">
            <span
              className={health?.db === "ok" ? "dot online" : "dot"}
              aria-hidden="true"
            />
            {error
              ? "Нет связи"
              : health?.db === "ok"
                ? "Сервис доступен"
                : "Проверка сервиса"}
          </span>
        </div>
      </header>
      <main id="main-content" tabIndex={-1}>
        <ErrorNotice
          message={error}
          retry={() => setHealthRevision((n) => n + 1)}
        />
        {health?.db === "error" && (
          <p className="notice danger" role="alert">
            Хранилище временно недоступно. Запросы могут завершаться ошибкой.
          </p>
        )}
        {health?.llm === "not_configured" && (
          <p className="notice warn llm-banner">
            <span aria-hidden="true">ⓘ</span>
            <span>
              <strong>LLM не настроена — заполнены только данные биржи.</strong>{" "}
              Текстовые риски и условия требуют проверки документов вручную.
            </span>
          </p>
        )}
        {page}
      </main>
      <footer className="app-footer">
        <strong>{DISCLAIMER}</strong>
        <span>Карточка облигации · рабочий инструмент аналитика</span>
      </footer>
    </>
  );
}
