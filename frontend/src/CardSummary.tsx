import { CouponBrief } from "./components";
import { displayValue, riskSummary, shortValue } from "./cardPresentation";
import { formatDate, isObject } from "./fields";
import type { Card } from "./types";
import { SecuritySticker } from "./art/Stickers";

export function jumpTo(id: string) {
  const target = document.getElementById(id);
  target?.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "instant"
      : "smooth",
    block: "start",
  });
  target?.focus({ preventScroll: true });
}
export function SectionLink({
  id,
  children,
}: {
  id: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={`#${id}`}
      onClick={(event) => {
        event.preventDefault();
        jumpTo(id);
      }}
    >
      {children}
    </a>
  );
}
export function CardSummary({ card }: { card: Card }) {
  const risks = Object.values(card.risk_fields).filter((f) => f != null);
  const counts = risks.reduce<Record<string, number>>((all, field) => {
    const label = riskSummary(field).label;
    all[label] = (all[label] ?? 0) + 1;
    return all;
  }, {});
  const schedule = displayValue(card.iss_fields.coupon_schedule);
  const today = new Date().toLocaleDateString("sv-SE");
  const next = Array.isArray(schedule?.payments)
    ? schedule.payments
        .filter(isObject)
        .filter((p) => String(p.payment_date) >= today)
        .sort((a, b) =>
          String(a.payment_date).localeCompare(String(b.payment_date)),
        )[0]
    : null;
  return (
    <section
      id="card-summary"
      className="card-summary card-section"
      tabIndex={-1}
    >
      <div className="section-heading">
        <h2>Сводка</h2>
        <div className="summary-counts" aria-label="Статусы рисковых условий">
          {Object.entries(counts).map(([label, count]) => (
            <span key={label}>
              <strong>{count}</strong> {label.toLocaleLowerCase("ru")}
            </span>
          ))}
        </div>
      </div>
      <div className="summary-stickers">
        {Object.values(card.iss_fields).some(field => field?.status === "verified_iss") && <SecuritySticker kind="moex" />}
        {risks.some(field => field.quote && field.status === "extracted") && <SecuritySticker kind="quote" />}
      </div>
      <dl className="key-facts">
        <div>
          <dt>Купон</dt>
          <dd>
            <CouponBrief value={displayValue(card.iss_fields.coupon_rate)} />
          </dd>
        </div>
        <div>
          <dt>Ближайшая выплата</dt>
          <dd>
            {next
              ? formatDate(String(next.payment_date))
              : "Нет будущих дат в графике"}
            <small>
              {schedule?.payments && Array.isArray(schedule.payments)
                ? `График: ${schedule.payments.length} выплат`
                : ""}
            </small>
          </dd>
        </div>
        <div>
          <dt>Оферты</dt>
          <dd>{shortValue(displayValue(card.iss_fields.offers))}</dd>
        </div>
        <div>
          <dt>Амортизация</dt>
          <dd>{shortValue(displayValue(card.iss_fields.amortization))}</dd>
        </div>
      </dl>
      <p className="caption summary-provenance">
        Погашение — в бланке выпуска выше. Параметры биржи справочные; сверка с
        документами — в разделе «Параметры».
      </p>
      <ul className="risk-summary">
        {risks.map((field) => {
          const info = riskSummary(field);
          return (
            <li key={field.code}>
              <strong>{info.title}</strong>
              <span className={`risk-label ${info.tone}`}>{info.label}</span>
              <p>{info.text}</p>
              <SectionLink id={`field-${field.code}`}>
                {field.quote ? "К цитате" : "Проверить условие"}
              </SectionLink>
            </li>
          );
        })}
      </ul>
      <p className="caption">
        Отсутствие условия в найденных документах не доказывает отсутствие
        обязательства.
      </p>
    </section>
  );
}
