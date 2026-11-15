import { enums, fieldTitles, formatDate, isObject } from "./fields";
import { safeUrl } from "./api";
import type { CardField, Json, Value } from "./types";

// An empty document schedule must not hide the exchange's known dates.
export function displayValue(field?: CardField): Value | null {
  if (!field) return null;
  if (
    field.code === "coupon_schedule" &&
    (!Array.isArray(field.value?.payments) || !field.value.payments.length)
  )
    return field.iss_value ?? field.value;
  return field.source.kind === "moex_iss"
    ? (field.iss_value ?? field.value)
    : field.value;
}
export function shortValue(value: Json | undefined): string {
  if (value == null) return "Нет данных";
  if (Array.isArray(value))
    return value.length
      ? shortValue(value[0]) +
          (value.length > 1 ? `; ещё ${value.length - 1}` : "")
      : "Не предусмотрено";
  if (isObject(value)) {
    if (value.amount != null && value.currency)
      return `${value.amount} ${value.currency}`;
    if (value.date) return formatDate(String(value.date));
    if (Array.isArray(value.payments))
      return value.payments.length
        ? `${value.payments.length} выплат`
        : "График не найден";
    if (value.current_rate_percent)
      return `${value.current_rate_percent}% (текущая ставка ISS)`;
    if (isObject(value.formula) && value.formula.text)
      return String(value.formula.text);
    if (Array.isArray(value.periods) && value.periods.length)
      return shortValue(value.periods);
    if (value.rate_percent) return `${value.rate_percent}%`;
    if (value.from != null && value.to != null)
      return `Купоны № ${value.from}–${value.to}${value.amount != null ? `: ${value.amount} (сумма)` : ""}`;
    const preferred = [
      "description",
      "condition",
      "trigger",
      "restriction",
      "text",
      "date_rule",
      "notice_rule",
      "scope",
      "submission",
      "type",
    ];
    for (const key of preferred)
      if (value[key] != null) return shortValue(value[key]);
    return (
      Object.values(value)
        .filter((v) => v != null)
        .map(shortValue)
        .filter(Boolean)
        .join(" · ") || "Нет данных"
    );
  }
  const text = String(value);
  return enums[text] ?? text;
}
export function riskSummary(field: CardField) {
  const known =
    ["extracted", "verified_iss"].includes(field.status) && field.value != null;
  return {
    label: known
      ? "Есть"
      : field.status === "not_found"
        ? "Нет в документах"
        : "Требует проверки",
    tone: known ? "good" : field.status === "not_found" ? "neutral" : "warn",
    text: known
      ? shortValue(field.value)
      : field.reason_message || "Сверьте условие с документами выпуска",
    title: fieldTitles[field.code],
  };
}
export function sourceUrl(
  url: string | null | undefined,
  page: number | null | undefined,
) {
  const safe = safeUrl(url);
  if (!safe || !page) return safe;
  const target = new URL(safe);
  target.hash = `page=${page}`;
  return target.href;
}
// Highlight only a literal fragment of the verified quote, never a paraphrase.
export function quoteParts(
  quote: string,
  value: Value | null,
): [string, string, string] {
  const strings = (node: Json | null): string[] =>
    typeof node === "string"
      ? [node]
      : Array.isArray(node)
        ? node.flatMap(strings)
        : isObject(node)
          ? Object.values(node).flatMap(strings)
          : [];
  for (const candidate of strings(value)
    .filter((s) => s.length >= 8)
    .sort((a, b) => b.length - a.length)) {
    const index = quote
      .toLocaleLowerCase("ru")
      .indexOf(candidate.toLocaleLowerCase("ru"));
    if (index >= 0)
      return [
        quote.slice(0, index),
        quote.slice(index, index + candidate.length),
        quote.slice(index + candidate.length),
      ];
  }
  // When structured values do not occur verbatim, emphasize the first sentence.
  const end = quote.search(/[.!?](?:\s|$)/);
  const length = end >= 0 ? end + 1 : Math.min(quote.length, 160);
  return ["", quote.slice(0, length), quote.slice(length)];
}
