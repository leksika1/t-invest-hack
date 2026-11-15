import { Rosette, PaperIllustration } from "./art/guilloche";
import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { CardPage } from "./CardView";
import { ErrorNotice, IsinForm, StatusBadge, Skeleton } from "./components";
import { formatDate, validIsin } from "./fields";
import type { CardSummary, Health } from "./types";
import { Workspace } from "./Workspace";
import "./styles.css";

export const DISCLAIMER =
  "Факты из документов эмитента, не инвестиционная рекомендация";
function Home() {
  return (
    <>
      <div className="home-layout">
        <div className="home-content">
          <div className="hero">
            <h1>
              Разберите условия
              <br />
              облигации по документам
            </h1>
            <p className="hero-copy">
              Сопоставьте параметры биржи с документами эмитента. Проверьте
              купоны, оферты и риски в одном месте.
            </p>
          </div>
          <section className="search-panel" aria-label="Открыть выпуск по ISIN">
            <IsinForm
              onSubmit={(isin) => {
                window.location.hash = "/issue/" + isin;
              }}
            />
            <p className="caption demo-caption">Попробуйте:</p>
            <div className="demo-options">
              <a href="#/issue/RU000A10ATW2">
                <strong>Балтийский лизинг</strong>
                <span>RU000A10ATW2</span>
                <small>Документы ПРАЙМ</small>
              </a>
              <a href="#/issue/RU000A10CS75">
                <strong>Аэрофлот</strong>
                <span>RU000A10CS75</span>
                <small>Документы СКРИН</small>
              </a>
            </div>
          </section>
        </div>
        <div className="home-art">
          <Rosette seed="RU000A10CS75" />
        </div>
      </div>
      <div className="intro-grid">
        {[
          [
            "1",
            "Подготовьте документы",
            "Автосбор из раскрытия или загрузка своих PDF или ZIP. Вы сами выбираете источники для сборки.",
          ],
          [
            "2",
            "Сопоставьте факты",
            "8 параметров выпуска со сверкой ISS и 7 групп рисковых условий из документов.",
          ],
          [
            "3",
            "Проверьте и сохраните",
            "Откройте цитаты, подтвердите или исправьте результат и сохраните PDF или Markdown.",
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
        <ErrorNotice
          message={error}
          retry={() => {
            setLoading(true);
            setRevision((n) => n + 1);
          }}
        />
        {loading ? (
          <Skeleton label="Загружаем историю карточек" />
        ) : !items.length && !error ? (
          <div className="empty-state">
            <p>Карточек пока нет</p>
            <p className="caption">
              {isin
                ? "Для этого выпуска ещё не сохранено карточек."
                : "Откройте выпуск и создайте первую карточку."}
            </p>
          </div>
        ) : error && !items.length ? null : (
          <div
            className="table-scroll"
            role="region"
            aria-label="История карточек"
            tabIndex={0}
          >
            <table className="history-table">
              <thead>
                <tr>
                  <th>ISIN</th>
                  <th>Выпуск</th>
                  <th>Дата сборки</th>
                  <th>Версия</th>
                  <th>Статусы полей</th>
                </tr>
              </thead>
              <tbody>
                {items.map((card) => (
                  <tr key={card.id}>
                    <td>
                      <a href={"#/cards/" + card.id}>{card.isin}</a>
                    </td>
                    <td>
                      {card.header?.name ||
                        card.header?.short_name ||
                        "Облигация"}
                    </td>
                    <td>{formatDate(card.created_at)}</td>
                    <td>{card.version}</td>
                    <td>
                      <div className="history-statuses">
                        <StatusBadge status={card.status} />
                        {card.field_statuses &&
                          Object.entries(card.field_statuses).map(
                            ([status, count]) => (
                              <span key={status}>
                                <StatusBadge status={status} /> {count}
                              </span>
                            ),
                          )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
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
            <Rosette className="brand-mark" seed="logo" />
            <span>Карточка облигации</span>
          </a>
          <nav aria-label="Основная навигация">
            <a href="#/" aria-current={!isHistory ? "page" : undefined}>
              Выпуск
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
          <p className="notice neutral llm-banner">
            <PaperIllustration kind="model" />
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
        <span>Данные и документы выпуска</span>
      </footer>
    </>
  );
}
