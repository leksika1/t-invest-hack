import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CouponRibbon } from "./CouponRibbon";
import { Rosette, rosettePaths } from "./art/guilloche";
import { FieldCard } from "./CardView";
import { CouponBrief, ValueView } from "./components";
import scheduleFixture from "./test/aeroflot-schedule.json";
import { displayValue, sourceUrl } from "./cardPresentation";
import { CardSummary } from "./CardSummary";
import { card, mismatch } from "./test/fixtures";

describe("Гильош и купоны", () => {
  it("показывает текущую ставку ISS без выдуманного номера купона", () => {
    render(
      <CouponBrief
        value={{ periods: [], formula: null, current_rate_percent: "12.5" }}
      />,
    );
    expect(screen.getByText(/12.5%/)).toBeVisible();
    expect(screen.getByText("(текущая ставка ISS)")).toBeVisible();
    expect(screen.queryByText(/купон №/)).not.toBeInTheDocument();
  });
  it("повторяет рисунок по ISIN и различает выпуски, кривые конечны", () => {
    expect(rosettePaths("RU000A10CS75")).toEqual(rosettePaths("RU000A10CS75"));
    expect(rosettePaths("RU000A10CS75")).not.toEqual(
      rosettePaths("RU000A10ATW2"),
    );
    expect(
      rosettePaths("RU000A10CS75").every(
        (p) => !/NaN|Infinity/.test(p) && p.endsWith(" Z"),
      ),
    ).toBe(true);
    const { container } = render(<Rosette seed="RU000A10CS75" />);
    expect(container.querySelector("svg")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
  });
  it("показывает известную сумму, ближайший купон и формулу, прокручивается с клавиатуры", () => {
    render(
      <CouponRibbon
        today="2026-11-15"
        schedule={{
          frequency_per_year: 12,
          payments: [
            { number: 1, payment_date: "2026-10-15" },
            { number: 2, payment_date: "2026-11-15" },
            { number: 3, payment_date: "2026-12-15" },
          ],
        }}
        rate={{
          periods: [
            { from: 1, to: 1, amount: "15.34" },
            { from: 2, to: 2, rate_percent: "17.5" },
          ],
        }}
      />,
    );
    expect(screen.getByText("15.34 ₽")).toBeVisible();
    expect(screen.getByText("17.5%")).toBeVisible();
    expect(screen.getByText("по формуле")).toBeVisible();
    expect(screen.getByText("Ближайший").closest("li")).toHaveClass("next");
    expect(screen.getByText("15.34 ₽").closest("li")).toHaveClass("paid");
    expect(
      screen.getByText(/3 купонов, ежемесячно, следующий 15.11.2026/),
    ).toBeVisible();
    const ribbon = screen.getByRole("list");
    ribbon.focus();
    fireEvent.keyDown(ribbon, { key: "ArrowRight" });
    expect(ribbon.scrollLeft).toBe(190);
    fireEvent.keyDown(ribbon, { key: "Home" });
    expect(ribbon.scrollLeft).toBe(0);
  });
  it("не обещает будущих выплат, если график завершён или отсутствует", () => {
    const view = render(
      <CouponRibbon
        today="2026-11-15"
        schedule={{ payments: [{ number: 1, payment_date: "2025-01-01" }] }}
        rate={null}
      />,
    );
    expect(screen.getByText(/график завершён/)).toBeVisible();
    expect(screen.queryByText("Ближайший")).not.toBeInTheDocument();
    view.rerender(<CouponRibbon schedule={null} rate={null} />);
    expect(screen.getByText(/График пока не найден/)).toBeVisible();
  });
  it("длинные графики раскрываются по действию", async () => {
    render(
      <ValueView
        value={Array.from({ length: 60 }, (_, i) => ({
          number: i + 1,
          payment_date: "2026-12-13",
        }))}
      />,
    );
    expect(screen.getByRole("table", { hidden: true })).not.toBeVisible();
    await userEvent.click(
      screen.getByText("Показать полный график (60 записей)"),
    );
    expect(screen.getByRole("table")).toBeVisible();
  });
  it("строка определения различает отсутствие оферт и отсутствие данных, сохраняет статус", () => {
    const field = {
      ...mismatch,
      code: "offers" as const,
      status: "extracted" as const,
      value: { events: [] },
      source: { kind: "moex_iss" },
    };
    const view = render(
      <dl>
        <FieldCard
          field={field}
          cardId="test"
          documents={[]}
          onChange={vi.fn()}
        />
      </dl>,
    );
    expect(screen.getByRole("term")).toHaveTextContent("Оферты");
    expect(
      within(screen.getByRole("definition")).getByText("Извлечено"),
    ).toBeVisible();
    expect(screen.getByText("Оферт нет по данным MOEX ISS")).toBeVisible();
    view.rerender(
      <dl>
        <FieldCard
          field={{
            ...field,
            value: null,
            status: "not_found",
            reason_message: "ISS недоступен",
          }}
          cardId="test"
          documents={[]}
          onChange={vi.fn()}
        />
      </dl>,
    );
    expect(
      screen.queryByText("Оферт нет по данным MOEX ISS"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("ISS недоступен")).toBeVisible();
  });
});

it("Аэрофлот: пустой график документа не скрывает 60 выплат ISS в ленте, сводке и поле", () => {
  const field = {
    ...mismatch,
    code: "coupon_schedule" as const,
    value: scheduleFixture.document_value,
    iss_value: scheduleFixture.iss_value,
    status: "verified_iss" as const,
  };
  const view = render(
    <CouponRibbon
      schedule={displayValue(field)}
      rate={null}
      today="2026-11-15"
    />,
  );
  expect(screen.getByRole("list").children).toHaveLength(60);
  expect(screen.queryByText(/График пока не найден/)).not.toBeInTheDocument();
  view.unmount();
  const summary = render(
    <CardSummary
      card={{
        ...card,
        iss_fields: { ...card.iss_fields, coupon_schedule: field },
      }}
    />,
  );
  expect(screen.getByText("График: 60 выплат")).toBeVisible();
  expect(screen.getByText("13.12.2026")).toBeVisible();
  summary.unmount();
  render(
    <dl>
      <FieldCard
        field={field}
        cardId="test"
        documents={[]}
        onChange={vi.fn()}
      />
    </dl>,
  );
  expect(screen.getByText(/60 купонных выплат/)).toBeVisible();
  expect(screen.queryByText("Список пуст")).not.toBeInTheDocument();
});
it("не заменяет непустой документный график биржевым и безопасно открывает страницу источника", () => {
  const field = {
    ...mismatch,
    code: "coupon_schedule" as const,
    value: { payments: [{ number: 1, payment_date: "2027-01-01" }] },
    iss_value: scheduleFixture.iss_value,
  };
  expect(displayValue(field)).toBe(field.value);
  expect(sourceUrl("https://example.org/report.pdf#old", 17)).toBe(
    "https://example.org/report.pdf#page=17",
  );
  expect(sourceUrl("javascript:alert(1)", 1)).toBeUndefined();
});
it("рисковая цитата сразу видна, выделение не меняет её текст, ссылка содержит страницу", () => {
  const quote =
    "Эмитент вправе досрочно погасить облигации по своему усмотрению.";
  render(
    <dl>
      <FieldCard
        field={{
          ...mismatch,
          code: "issuer_call_option",
          status: "extracted",
          value: { conditions: [{ trigger: "по своему усмотрению" }] },
          quote,
          source: {
            kind: "document",
            name: "Условия выпуска.pdf",
            url: "https://example.org/terms.pdf",
          },
          page: 12,
          clause: "6.4",
        }}
        cardId="test"
        documents={[]}
        onChange={vi.fn()}
      />
    </dl>,
  );
  expect(
    screen.getByText("по своему усмотрению", { selector: "mark" }),
  ).toBeVisible();
  expect(document.querySelector("blockquote")).toHaveTextContent(quote);
  expect(
    screen.getByRole("link", { name: /Условия выпуска.pdf, стр. 12, п. 6.4/ }),
  ).toHaveAttribute("href", "https://example.org/terms.pdf#page=12");
});

it("частично подтверждённый график сохраняет полный календарь ISS в деталях", async () => {
  const field = {
    ...mismatch,
    code: "coupon_schedule" as const,
    status: "verified_iss" as const,
    value: { payments: [{ number: 1, payment_date: "2025-10-19" }] },
    iss_value: scheduleFixture.iss_value,
  };
  render(
    <dl>
      <FieldCard
        field={field}
        cardId="test"
        documents={[]}
        onChange={vi.fn()}
      />
    </dl>,
  );
  expect(
    screen.getByRole("heading", { name: "Данные MOEX ISS" }),
  ).toBeVisible();
  expect(screen.getByText("Показать полный график (60 записей)")).toBeVisible();
});
