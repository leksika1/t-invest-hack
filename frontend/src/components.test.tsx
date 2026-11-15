import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { IsinForm, StatusBadge, ExternalLink, ValueEditor } from "./components";
import { FieldCard } from "./CardView";
import { api } from "./api";
import { editors, initialValue, validIsin } from "./fields";
import { useState } from "react";
import type { Json } from "./types";
import { validateFiles } from "./Workspace";
import { cardId, document, mismatch } from "./test/fixtures";

describe("Статусы и валидация", () => {
  it.each([
    ["verified_iss", "Сверено с ISS"],
    ["mismatch_iss", "Расхождение с ISS"],
    ["not_found", "Не найдено"],
    ["needs_review", "Нужна проверка"],
    ["extracted", "Извлечено"],
    ["confirmed", "Подтверждено человеком"],
    ["corrected", "Исправлено человеком"],
  ])("%s имеет текст и иконку", (status, label) => {
    const { container } = render(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toBeVisible();
    expect(
      container.querySelector('[aria-hidden="true"]'),
    ).not.toBeEmptyDOMElement();
  });
  it("не отправляет неверный ISIN, нормализует корректный", async () => {
    const submit = vi.fn();
    const user = userEvent.setup();
    render(<IsinForm onSubmit={submit} />);
    await user.type(screen.getByLabelText("ISIN выпуска"), "XX123");
    await user.click(screen.getByRole("button", { name: /Открыть выпуск/ }));
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByLabelText("ISIN выпуска")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await user.clear(screen.getByLabelText("ISIN выпуска"));
    await user.type(screen.getByLabelText("ISIN выпуска"), " ru000a10atw2 ");
    await user.click(screen.getByRole("button", { name: /Открыть выпуск/ }));
    expect(submit).toHaveBeenCalledWith("RU000A10ATW2");
    expect(validIsin("RU000A10CS75")).toBe(true);
    expect(validIsin("RU000A10ATWX")).toBe(false);
  });
  it("не допускает исполняемые внешние ссылки", () => {
    render(<ExternalLink url="javascript:alert(1)">Источник</ExternalLink>);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
  it("отклоняет пустой, большой PDF, посторонний формат и пакет > 5", () => {
    const pdf = new File(["pdf"], "a.pdf", { type: "application/pdf" });
    const big = new File(["x"], "b.pdf");
    Object.defineProperty(big, "size", { value: 31 * 1024 * 1024 });
    expect(validateFiles([pdf])).toBeNull();
    expect(validateFiles([big])).toMatch(/30 МиБ/);
    expect(validateFiles([new File([], "empty.pdf")])).toMatch(/непустым/);
    expect(validateFiles([new File(["zip"], "a.zip")])).toBeNull();
    expect(validateFiles([new File(["rar"], "a.rar")])).toMatch(
      /только PDF или ZIP/,
    );
    expect(validateFiles(Array(6).fill(pdf))).toMatch(/1 до 5/);
  });
});
describe("Поле карточки", () => {
  it("форма составного поля добавляет и удаляет элементы и необязательную формулу", async () => {
    function Editor() {
      const [value, setValue] = useState<Json>(
        initialValue(editors.coupon_rate),
      );
      return (
        <ValueEditor
          node={editors.coupon_rate}
          value={value}
          onChange={setValue}
          title="Ставка купона"
        />
      );
    }
    const user = userEvent.setup();
    render(<Editor />);
    await user.click(screen.getByRole("button", { name: "Добавить: Периоды" }));
    expect(screen.getByLabelText("С купона №")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Добавить: Формула" }));
    expect(screen.getByLabelText("Формулировка")).toBeRequired();
    await user.click(screen.getByRole("button", { name: "Не указывать" }));
    expect(screen.queryByLabelText("Формулировка")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Удалить Периоды 1" }));
    expect(screen.queryByLabelText("С купона №")).not.toBeInTheDocument();
  });
  it("показывает обе суммы и привязку цитаты при mismatch", async () => {
    render(
      <FieldCard
        field={mismatch}
        cardId={cardId}
        documents={[document]}
        onChange={vi.fn()}
      />,
    );
    expect(
      within(
        screen.getByRole("heading", { name: "В документе" }).parentElement!,
      ).getByText("1000 RUB"),
    ).toBeVisible();
    expect(
      within(
        screen.getByRole("heading", { name: "В MOEX ISS" }).parentElement!,
      ).getByText("900 RUB"),
    ).toBeVisible();
    expect(screen.getByText(/стр. 3, п. 3.1/)).toBeVisible();
    await userEvent.click(screen.getByText("Цитаты и источники"));
    expect(screen.getByText(mismatch.quote!)).toBeVisible();
  });
  it("скрытое значение нельзя подтвердить; причина видна", () => {
    render(
      <FieldCard
        field={{
          ...mismatch,
          value: null,
          quote: null,
          document_id: null,
          source: {},
          status: "needs_review",
          reason_message: "Цитата не подтверждена",
        }}
        cardId={cardId}
        documents={[]}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText("Цитата не подтверждена")).toBeVisible();
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeDisabled();
    expect(screen.queryByText(mismatch.quote!)).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Данные MOEX ISS" }),
    ).toBeVisible();
    expect(screen.getByText("900 RUB")).toBeVisible();
  });
  it("показывает ISS и пометку при частичной сверке документа", () => {
    render(
      <FieldCard
        field={{
          ...mismatch,
          status: "verified_iss",
          reason: "iss_partial_confirmation",
          reason_message:
            "Документ подтверждает только часть параметров; полные данные — в MOEX ISS",
        }}
        cardId={cardId}
        documents={[document]}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText("1000 RUB")).toBeVisible();
    expect(screen.getByText("900 RUB")).toBeVisible();
    expect(
      screen.getByText(/Документ подтверждает только часть параметров/),
    ).toBeVisible();
  });
  it("исправляет типизированное значение через PATCH, сохраняет исходное", async () => {
    const user = userEvent.setup();
    const change = vi.fn();
    const review = vi.spyOn(api, "review").mockResolvedValue({
      ...mismatch,
      user_status: "corrected",
      user_value: { amount: "1100", currency: "RUB" },
    });
    render(
      <FieldCard
        field={mismatch}
        cardId={cardId}
        documents={[document]}
        onChange={change}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Исправить" }));
    await user.clear(screen.getByLabelText("Сумма"));
    await user.type(screen.getByLabelText("Сумма"), "1100");
    await user.click(
      screen.getByRole("button", { name: "Сохранить исправление" }),
    );
    expect(review).toHaveBeenCalledWith(cardId, "face_value", {
      user_status: "corrected",
      user_value: { amount: "1100", currency: "RUB" },
    });
    expect(change).toHaveBeenCalled();
    expect(screen.getByText("1000 RUB")).toBeVisible();
  });
  it("подтверждает без user_value и показывает ошибку сохранения", async () => {
    const review = vi
      .spyOn(api, "review")
      .mockRejectedValue(new Error("Карточка ещё не готова"));
    render(
      <FieldCard
        field={mismatch}
        cardId={cardId}
        documents={[document]}
        onChange={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    expect(review).toHaveBeenCalledWith(cardId, "face_value", {
      user_status: "confirmed",
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Карточка ещё не готова",
    );
  });
});
