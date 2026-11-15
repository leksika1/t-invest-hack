import type {
  Card,
  CardField,
  DocumentInfo,
  DocumentList,
  Issue,
  Job,
} from "../types";
export const isin = "RU000A10ATW2";
export const cardId = "44444444-4444-4444-8444-444444444444";
export const jobId = "55555555-5555-4555-8555-555555555555";
export const document: DocumentInfo = {
  id: "11111111-1111-4111-8111-111111111111",
  filename: "Решение о выпуске.pdf",
  type: "decision",
  enabled: true,
  status: "ready",
  pages: 8,
  ocr_pages: 2,
  error: null,
  created_at: "2026-11-15T09:00:00Z",
  origins: [
    {
      source: "prime",
      source_url: "https://example.org/issuer",
      download_url: "https://example.org/decision.pdf",
      published_at: null,
      fetched_at: "2026-11-15T09:00:00Z",
      archive: null,
    },
  ],
};
export const mismatch: CardField = {
  code: "face_value",
  value: { amount: "1000", currency: "RUB" },
  iss_value: { amount: "900", currency: "RUB" },
  quote: "Номинальная стоимость облигации составляет 1 000 рублей.",
  document_id: document.id,
  page: 3,
  clause: "3.1",
  status: "mismatch_iss",
  confidence: 0.98,
  source: { kind: "document", name: document.filename, url: null },
  evidence: [],
  reason: null,
  reason_message: null,
  not_found: false,
  user_status: null,
  user_value: null,
};
export const issue: Issue = {
  isin,
  header: {
    issuer: "ООО «Балтийский лизинг»",
    inn: "7812000000",
    name: "Балтийский лизинг · БО-П13",
    listing_level: "2",
  },
  iss: { status: "available", fetched_at: "2026-11-15T09:00:00Z" },
  normalized: {
    fields: {
      face_value: { amount: "1000", currency: "RUB" },
      placement_date: { date: "2025-01-10" },
      maturity_date: { date: "2028-01-10" },
      coupon_type: null,
      coupon_rate: {
        periods: [{ from: 1, to: 36, rate_percent: "18.5", amount: null }],
        formula: null,
      },
    },
  },
  documents: [document],
  warnings: ["coupon_type_not_explicit_in_iss"],
};
export const listing: DocumentList = {
  items: [document],
  total: 1,
  limit: 100,
  offset: 0,
  sources: [
    { source: "prime", status: "succeeded", documents_found: 1 },
    { source: "skrin", status: "empty", documents_found: 0 },
    {
      source: "edisclosure",
      status: "official_access_required",
      documents_found: 0,
    },
  ],
  assisted: {
    source: "edisclosure",
    search_url: "https://www.e-disclosure.ru/poisk-po-kompaniyam",
    query: "7812000000",
    mode: "manual_query",
    message: "",
  },
  warnings: [],
  active_collect_job_id: null,
};
export const job: Job = {
  id: jobId,
  type: "build_card",
  status: "succeeded",
  step: "finalize",
  progress: 100,
  result: {},
  error: null,
  processed_fields: 15,
  total_fields: 15,
};
export const card: Card = {
  id: cardId,
  isin,
  version: 2,
  status: "ready",
  job_id: jobId,
  created_at: "2026-11-15T10:00:00Z",
  updated_at: "2026-11-15T10:00:00Z",
  header: issue.header,
  documents: [document],
  iss_fields: {
    face_value: mismatch,
    placement_date: {
      ...mismatch,
      code: "placement_date",
      status: "verified_iss",
      value: { date: "2025-01-10" },
      iss_value: { date: "2025-01-10" },
    },
  },
  risk_fields: {
    covenants: {
      ...mismatch,
      code: "covenants",
      value: {
        items: [
          {
            condition: "Отношение долга к EBITDA не более 3.",
            test_date_rule: "Ежеквартально",
            consequence: "Досрочное погашение по требованию владельцев",
            exceptions: null,
          },
        ],
      },
      iss_value: null,
      status: "extracted",
      quote:
        "Эмитент обязуется поддерживать отношение долга к EBITDA не более 3.",
    },
    cross_default: {
      ...mismatch,
      code: "cross_default",
      value: null,
      iss_value: null,
      quote: null,
      document_id: null,
      source: {},
      status: "needs_review",
      reason: "quote_not_verified",
      reason_message: "Цитата не подтверждена",
    },
  },
  journal: {
    documents_used: 1,
    pages: 8,
    ocr_pages: 2,
    models: ["Тестовая модель"],
    cost_usd: "0.012",
    duration_ms: 7200,
    warnings: [],
  },
  disclaimer: "Факты из документов эмитента, не инвестиционная рекомендация",
};
