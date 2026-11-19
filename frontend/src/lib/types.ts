export type Direction = "under_collected" | "over_collected" | "unverified";
export type Severity = "high" | "medium" | "low" | "info" | "none";

export type User = { id: string; email: string };

export type RejectedRow = { row_number: number; reasons: string[]; linked_order_key: string | null };
export type IdConflict = { key: string; row_numbers: number[]; order_keys: string[] };
export type FileReport = {
  file: "orders" | "payments";
  row_count: number;
  accepted: number;
  rejected_count: number;
  duplicate_rows_collapsed: number;
  rejected: RejectedRow[];
  id_conflicts: IdConflict[];
};

export type CurrencyMetrics = {
  orders: number;
  reconciled_value: string;
  reconciled_orders: number;
  disputed_value: string;
  settled_charge_value: string;
  at_risk: { under_collected: string; under_collected_pending: string; over_collected: string; unverified: string };
  categories: Record<string, { count: number; amount_at_risk: string }>;
};

export type Totals = {
  unique_orders: number;
  order_rows: number;
  payment_events: number;
  payment_rows: number;
  payment_events_by_type_status: Record<string, number>;
  items: number;
  needs_review: number;
  category_counts: Record<string, number>;
};

export type ImportSummary = {
  metrics: { totals: Totals; by_currency: Record<string, CurrencyMetrics> };
  validation: { orders: FileReport; payments: FileReport };
};

export type ImportRecord = {
  id: string;
  orders_filename: string;
  payments_filename: string;
  rules_version: string;
  created_at: string;
  summary: ImportSummary;
};

export type HistoryRow = Omit<ImportRecord, "summary"> & { totals: Totals | null; rejected_rows: number };

export type Item = {
  id: string;
  item_key: string;
  order_key: string | null;
  category: string;
  severity: Severity;
  currency: string | null;
  expected_amount: string | null;
  actual_amount: string | null;
  difference: string | null;
  reconciled_value: string;
  disputed_value: string;
  amount_at_risk: string;
  risk_direction: Direction | null;
  reason: string;
  dq_flags: string[];
};

export type EvidenceEvent = {
  transaction_ref: string;
  row_numbers: number[];
  type: string;
  status: string;
  currency: string;
  amount: string;
  fee: string | null;
  processed_at: string | null;
  dq_flags: string[];
};

export type Evidence = {
  rule: { number: number | null; category: string; rules_version: string };
  tolerance: string;
  order: {
    order_id_raw: string;
    row_numbers: number[];
    status: string;
    currency: string;
    net_amount: string;
    gross_amount: string | null;
    discount: string | null;
    order_date: string | null;
    customer_email: string | null;
  } | null;
  events: EvidenceEvent[];
  excess_refund?: string;
  timing_breaches?: { transaction_ref: string; hours_after_order: number }[];
  review_reasons?: string[];
};

export type ItemExplanation = {
  summary: string;
  likely_cause: string;
  recommended_actions: string[];
  confidence: "low" | "medium" | "high";
};
export type SummaryExplanation = { headline: string; top_issues: string[]; recommended_priorities: string[] };

export type Explanation<T> = {
  output: T;
  source: "llm" | "fallback";
  cached: boolean;
  model: string | null;
  prompt_version: string;
  created_at: string | null;
};

export type ItemPage = { items: Item[]; total: number; page: number; page_size: number };
