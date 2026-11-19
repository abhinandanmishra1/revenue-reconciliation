export type SortField = "priority" | "amount_at_risk" | "difference" | "order_key" | "category";

export type Filters = {
  category: string[];
  direction: string[];
  severity: string;
  currency: string;
  has_dq: boolean;
  q: string;
  sort: SortField;
  order: "asc" | "desc";
  page: number;
  /** Human label of what set the current filter (a KPI or a chart bar). */
  source: string | null;
};

export const DEFAULT_FILTERS: Filters = {
  category: [],
  direction: [],
  severity: "",
  currency: "",
  has_dq: false,
  q: "",
  sort: "priority",
  order: "desc",
  page: 1,
  source: null,
};

export const PAGE_SIZE = 25;

export function toQuery(f: Filters): string {
  const p = new URLSearchParams();
  f.category.forEach((c) => p.append("category", c));
  f.direction.forEach((d) => p.append("direction", d));
  if (f.severity) p.set("severity", f.severity);
  if (f.currency) p.set("currency", f.currency);
  if (f.has_dq) p.set("has_dq", "true");
  if (f.q.trim()) p.set("q", f.q.trim());
  p.set("sort", f.sort);
  p.set("order", f.order);
  p.set("page", String(f.page));
  p.set("page_size", String(PAGE_SIZE));
  return p.toString();
}
