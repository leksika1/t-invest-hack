import type { FieldCode, Json, Value } from "./types";

export const fieldTitles: Record<FieldCode, string> = {
  face_value: "Номинал и валюта",
  placement_date: "Дата размещения",
  maturity_date: "Дата погашения",
  coupon_type: "Тип купона",
  coupon_rate: "Ставка или формула купона",
  coupon_schedule: "График купонов",
  offers: "Оферты",
  amortization: "Амортизация",
  covenants: "Ковенанты",
  holder_early_redemption: "Погашение по требованию владельцев",
  issuer_call_option: "Call-опцион эмитента",
  guarantee_security: "Обеспечение",
  cross_default: "Кросс-дефолт",
  issuer_restrictions: "Ограничения эмитента",
  offer_procedure: "Порядок участия в оферте",
};
export const labels: Record<string, string> = {
  amount: "Сумма",
  currency: "Валюта",
  date: "Дата",
  type: "Тип",
  description: "Описание",
  periods: "Периоды",
  from: "С купона №",
  to: "По купон №",
  rate_percent: "Ставка, %",
  formula: "Формула",
  text: "Формулировка",
  base: "Базовая ставка",
  spread_pp: "Спред, п. п.",
  cap_percent: "Верхняя граница, %",
  floor_percent: "Нижняя граница, %",
  observation_rule: "Правило наблюдения",
  rounding_rule: "Округление",
  frequency_per_year: "Выплат в год",
  period_days: "Период, дней",
  total_payments: "Всего купонов",
  payments: "Выплаты",
  number: "№ купона",
  start: "Начало периода",
  end: "Конец периода",
  payment_date: "Дата выплаты",
  events: "События",
  price_percent: "Цена, % номинала",
  percent: "Доля, %",
  percent_base: "База расчёта",
  items: "Условия",
  condition: "Условие",
  test_date_rule: "Дата проверки",
  consequence: "Последствия",
  exceptions: "Исключения",
  grounds: "Основания",
  trigger: "Основание",
  deadline: "Срок",
  conditions: "Условия",
  date_rule: "Дата / правило",
  price_rule: "Цена / правило",
  notice_rule: "Уведомление",
  party: "Лицо, предоставляющее обеспечение",
  scope: "Объём",
  expiry: "Срок действия",
  threshold: "Порог",
  entities: "Круг лиц",
  grace_period: "Льготный период",
  action: "Действие",
  restriction: "Ограничение",
  procedures: "Порядок",
  offer_date: "Дата оферты",
  window_start: "Начало окна",
  window_end: "Конец окна",
  window_rule: "Правило определения окна",
  via: "Через кого",
  submission: "Способ подачи",
  settlement_rule: "Порядок расчётов",
  raw_type: "Тип в ISS",
};
export const enums: Record<string, string> = {
  fixed: "Фиксированный",
  floating: "Плавающий",
  variable: "Переменный",
  unknown: "Не определено",
  put: "Put — право владельца",
  call: "Call — право эмитента",
  surety: "Поручительство",
  third_party_offer: "Оферта третьего лица",
  pledge: "Залог",
  other: "Другое",
  initial_face_value: "Первоначальный номинал",
  remaining_face_value: "Остаточный номинал",
};
export type EditorNode =
  | { kind: "text" | "decimal" | "date" | "integer"; optional?: boolean }
  | { kind: "select"; options: string[]; optional?: boolean }
  | { kind: "object"; children: Record<string, EditorNode>; optional?: boolean }
  | { kind: "list"; item: EditorNode; optional?: boolean };
const text = (optional = false): EditorNode => ({ kind: "text", optional });
const decimal = (optional = true): EditorNode => ({
  kind: "decimal",
  optional,
});
const date = (optional = true): EditorNode => ({ kind: "date", optional });
const integer = (optional = true): EditorNode => ({
  kind: "integer",
  optional,
});
const select = (...options: string[]): EditorNode => ({
  kind: "select",
  options,
});
const object = (
  children: Record<string, EditorNode>,
  optional = false,
): EditorNode => ({ kind: "object", children, optional });
const list = (item: EditorNode): EditorNode => ({ kind: "list", item });
const money = object({ amount: decimal(false), currency: text() });
export const editors: Record<FieldCode, EditorNode> = {
  face_value: money,
  placement_date: object({ date: date(false) }),
  maturity_date: object({ date: date(false) }),
  coupon_type: object({
    type: select("fixed", "floating", "variable", "unknown"),
    description: text(true),
  }),
  coupon_rate: object({
    periods: list(
      object({
        from: integer(false),
        to: integer(false),
        rate_percent: decimal(),
        amount: decimal(),
      }),
    ),
    formula: object(
      {
        text: text(),
        base: text(true),
        spread_pp: decimal(),
        cap_percent: decimal(),
        floor_percent: decimal(),
        observation_rule: text(true),
        rounding_rule: text(true),
      },
      true,
    ),
  }),
  coupon_schedule: object({
    total_payments: integer(),
    frequency_per_year: integer(),
    period_days: integer(),
    payments: list(
      object({
        number: integer(false),
        start: date(),
        end: date(),
        payment_date: date(false),
      }),
    ),
  }),
  offers: object({
    events: list(
      object({
        date: date(false),
        type: select("put", "call", "unknown"),
        price_percent: decimal(),
      }),
    ),
  }),
  amortization: object({
    events: list(
      object({
        date: date(false),
        amount: decimal(),
        percent: decimal(),
        percent_base: select(
          "initial_face_value",
          "remaining_face_value",
          "unknown",
        ),
      }),
    ),
  }),
  covenants: object({
    items: list(
      object({
        condition: text(),
        test_date_rule: text(true),
        consequence: text(true),
        exceptions: text(true),
      }),
    ),
  }),
  holder_early_redemption: object({
    grounds: list(
      object({ trigger: text(), deadline: text(true), exceptions: text(true) }),
    ),
  }),
  issuer_call_option: object({
    conditions: list(
      object({
        trigger: text(),
        date_rule: text(true),
        price_rule: text(true),
        notice_rule: text(true),
      }),
    ),
  }),
  guarantee_security: object({
    items: list(
      object({
        type: select("surety", "third_party_offer", "pledge", "other"),
        party: text(true),
        scope: text(),
        expiry: text(true),
        exceptions: text(true),
      }),
    ),
  }),
  cross_default: object({
    items: list(
      object({
        trigger: text(),
        threshold: object({ amount: decimal(false), currency: text() }, true),
        entities: text(true),
        grace_period: text(true),
        consequence: text(true),
        exceptions: text(true),
      }),
    ),
  }),
  issuer_restrictions: object({
    items: list(
      object({
        action: text(),
        restriction: text(),
        exceptions: text(true),
        consequence: text(true),
      }),
    ),
  }),
  offer_procedure: object({
    procedures: list(
      object({
        offer_date: date(),
        window_start: date(),
        window_end: date(),
        window_rule: text(true),
        via: text(true),
        submission: text(),
        settlement_rule: text(true),
      }),
    ),
  }),
};
export function initialValue(node: EditorNode): Json {
  if (node.optional) return null;
  if (node.kind === "object")
    return Object.fromEntries(
      Object.entries(node.children).map(([key, child]) => [
        key,
        initialValue(child),
      ]),
    );
  if (node.kind === "list") return [];
  if (node.kind === "select") return node.options[0];
  return "";
}
export function isObject(value: Json | undefined): value is Value {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
export const normalizeIsin = (value: string) => value.trim().toUpperCase();
export const validIsin = (value: string) =>
  /^RU[A-Z0-9]{9}[0-9]$/.test(normalizeIsin(value));
export const formatDate = (value: string | null | undefined) =>
  value
    ? /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? value.split("-").reverse().join(".")
      : new Date(value).toLocaleString("ru-RU")
    : "Не указано";
export const sourceName = (value: string) =>
  (
    ({
      prime: "ПРАЙМ",
      skrin: "СКРИН",
      edisclosure: "e-disclosure",
      upload: "Ручная загрузка",
      moex_iss: "MOEX ISS",
    }) as Record<string, string>
  )[value] ?? value;
export const warningText = (value: string) =>
  (
    ({
      initial_face_value_unavailable:
        "ISS не содержит подтверждённого первоначального номинала.",
      coupon_type_not_explicit_in_iss:
        "Тип купона нельзя определить только по ISS.",
      coupon_type_inferred_from_iss:
        "Тип купона предположен по графику ISS. Проверьте его и формулу в документах.",
      offer_type_requires_document:
        "Тип оферты требует проверки по документам.",
      iss_unavailable: "ISS временно недоступен.",
      iss_unavailable_or_stale: "ISS недоступен: сохранён предыдущий снимок.",
      some_documents_not_ready:
        "Часть документов не распознана — проверьте статусы.",
      embeddings_failed_fts_only:
        "Векторный поиск недоступен, использован текстовый поиск.",
      embeddings_not_configured_fts_only: "Использован текстовый поиск.",
      query_embeddings_failed_fts_only: "Использован текстовый поиск.",
      iss_only_no_documents:
        "Нет выбранных документов: доступны только данные ISS.",
    }) as Record<string, string>
  )[value] ?? value;
