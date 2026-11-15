import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CouponRibbon } from "./CouponRibbon";
import { Rosette, rosettePaths } from "./art/guilloche";
import { FieldCard } from "./CardView";
import { CouponBrief, ValueView } from "./components";
import { mismatch } from "./test/fixtures";

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
