import { useEffect, useRef } from "react";
import { formatDate, isObject } from "./fields";
import type { Json } from "./types";

export function CouponRibbon({
  schedule,
  rate,
  today = new Date().toLocaleDateString("sv-SE"),
}: {
  schedule: Json | undefined;
  rate: Json | undefined;
  today?: string;
}) {
  const nextRef = useRef<HTMLLIElement>(null);
  const payments =
    isObject(schedule) && Array.isArray(schedule.payments)
      ? schedule.payments.filter(isObject)
      : [];
  const periods =
    isObject(rate) && Array.isArray(rate.periods)
      ? rate.periods.filter(isObject)
      : [];
  const next = payments.find((p) => String(p.payment_date) >= today);
  useEffect(() => {
    const node = nextRef.current;
    if (node?.parentElement)
      node.parentElement.scrollLeft = Math.max(
        0,
        node.offsetLeft - node.parentElement.offsetLeft - 16,
      );
  }, [schedule, today]);
  if (!payments.length)
    return (
      <section className="coupon-section">
        <h2>Купоны</h2>
        <p className="muted">
          График пока не найден. Проверьте документы выпуска.
        </p>
      </section>
    );
  const frequency = isObject(schedule) ? schedule.frequency_per_year : null;
  const cadence =
    frequency === 12
      ? "ежемесячно"
      : frequency === 4
        ? "ежеквартально"
        : frequency
          ? `${frequency} выплат в год`
          : "по графику";
  return (
    <section className="coupon-section" aria-label="Лента купонов">
      <div className="section-heading">
        <h2>Купоны</h2>
        <p className="caption">
          {payments.length} купонов, {cadence}
          {next
            ? `, следующий ${formatDate(String(next.payment_date))}`
            : ", график завершён"}
        </p>
      </div>
      <ol
        className="coupon-ribbon"
        tabIndex={0}
        aria-label="График выплат, прокручивается стрелками"
        onKeyDown={(e) => {
          if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
            e.preventDefault();
            e.currentTarget.scrollLeft += e.key === "ArrowRight" ? 190 : -190;
          }
          if (e.key === "Home" || e.key === "End") {
            e.preventDefault();
            e.currentTarget.scrollLeft =
              e.key === "Home" ? 0 : e.currentTarget.scrollWidth;
          }
        }}
      >
        {payments.map((p, i) => {
          const period = periods.find(
            (r) =>
              Number(r.from) <= Number(p.number) &&
              Number(r.to) >= Number(p.number),
          );
          const past = String(p.payment_date) < today;
          const isNext = next === p;
          return (
            <li
              key={String(p.number)}
              ref={isNext ? nextRef : undefined}
              className={`coupon print-${Math.min(i, 12)} ${past ? "paid" : ""} ${isNext ? "next" : ""}`}
            >
              <span className="coupon-number">
                Купон № {String(p.number)}
                {isNext && <span className="next-label">Ближайший</span>}
              </span>
              <time dateTime={String(p.payment_date)}>
                {formatDate(String(p.payment_date))}
              </time>
              <strong>
                {period?.amount != null
                  ? `${period.amount} ₽`
                  : period?.rate_percent != null
                    ? `${period.rate_percent}%`
                    : "по формуле"}
              </strong>
              <span className="caption">
                {past
                  ? "Дата прошла"
                  : period?.amount != null
                    ? "На одну облигацию"
                    : "Условия в документах"}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="caption ribbon-hint">
        Прокрутите ленту, чтобы увидеть весь график. Даты по данным источника;
        фактические выплаты не проверялись.
      </p>
    </section>
  );
}
