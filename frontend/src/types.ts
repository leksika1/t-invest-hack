export type Json =
  string | number | boolean | null | Json[] | { [key: string]: Json };
export type Value = { [key: string]: Json };
export type Health = {
  status: string;
  db: string;
  llm: string;
  llm_proxy?: "configured" | "none";
  llm_proxy_scheme?: "http" | "https" | "socks5" | "socks5h" | null;
  version: string;
};
export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};
export type Problem = { code?: string; message: string };
export type Origin = {
  source: string;
  source_url: string | null;
  download_url: string | null;
  published_at: string | null;
  fetched_at: string;
  archive: { url: string; entry_name: string } | null;
};
export type DocumentInfo = {
  id: string;
  filename: string;
  type: string;
  enabled: boolean;
  status: string;
  pages: number;
  ocr_pages: number;
  error: Problem | null;
  origins: Origin[];
  created_at: string;
  sha256?: string;
  size_bytes?: number;
};
export type Source = {
  source: string;
  status: string;
  documents_found: number;
  message?: string | null;
  checked_at?: string;
  errors?: Problem[];
};
export type Assisted = {
  source: string;
  search_url: string | null;
  query: string;
  mode: string;
  message: string;
};
export type DocumentList = Page<DocumentInfo> & {
  sources: Source[];
  assisted: Assisted;
  active_collect_job_id: string | null;
  warnings: string[];
};
export type Header = {
  issuer?: string | null;
  inn?: string | null;
  name?: string | null;
  short_name?: string | null;
  listing_level?: string | null;
  registration_number?: string | null;
};
export type Issue = {
  isin: string;
  header: Header;
  iss: { status: string; fetched_at: string | null };
  normalized?: { fields?: Record<string, Value | null>; warnings?: string[] };
  documents: DocumentInfo[];
  warnings: string[];
};
export type Job = {
  id: string;
  type: string;
  status: "queued" | "running" | "succeeded" | "failed";
  step: string;
  progress: number;
  result?: {
    sources?: Source[];
    assisted?: Assisted;
    warnings?: string[];
    card_id?: string;
  };
  error: Problem | null;
  card_id?: string;
  processed_fields?: number;
  total_fields?: number;
};
export type FieldStatus =
  "verified_iss" | "mismatch_iss" | "extracted" | "not_found" | "needs_review";
export type FieldCode =
  | "face_value"
  | "placement_date"
  | "maturity_date"
  | "coupon_type"
  | "coupon_rate"
  | "coupon_schedule"
  | "offers"
  | "amortization"
  | "covenants"
  | "holder_early_redemption"
  | "issuer_call_option"
  | "guarantee_security"
  | "cross_default"
  | "issuer_restrictions"
  | "offer_procedure";
export type Evidence = {
  quote: string;
  document_id: string;
  page: number;
  clause: string | null;
  value_path?: string | null;
};
export type CardField = {
  code: FieldCode;
  value: Value | null;
  quote: string | null;
  document_id: string | null;
  page: number | null;
  clause: string | null;
  status: FieldStatus;
  iss_value: Value | null;
  confidence: number | null;
  source: { kind?: string; name?: string | null; url?: string | null };
  evidence: Evidence[];
  reason: string | null;
  reason_message?: string | null;
  not_found: boolean;
  user_status: "confirmed" | "corrected" | null;
  user_value: Value | null;
  reviewed_at?: string | null;
};
export type CardSummary = {
  header?: Header;
  field_statuses?: Record<string, number>;
  id: string;
  isin: string;
  version: number;
  status: "processing" | "ready" | "failed";
  created_at: string;
  updated_at: string;
};
export type Card = CardSummary & {
  job_id: string | null;
  header: Header;
  documents: DocumentInfo[];
  iss_fields: Partial<Record<FieldCode, CardField>>;
  risk_fields: Partial<Record<FieldCode, CardField>>;
  journal: {
    documents_used?: number;
    pages?: number;
    ocr_pages?: number;
    models?: string[];
    cost_usd?: string | null;
    known_cost_usd?: string;
    unknown_cost_calls?: number;
    duration_ms?: number;
    warnings?: string[];
    errors?: Problem[];
  };
  disclaimer: string;
};
export type Review =
  | { user_status: "confirmed" }
  | { user_status: "corrected"; user_value: Value };
