import type { Direction, Severity } from "./types";

export function money(value: string | null | undefined, currency: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = Number(value); // display only; all arithmetic happens on the server in Decimal
  if (!currency) return n.toFixed(2);
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency}`;
  }
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Source timestamps are timezone-naive: show them exactly as written. */
export function naiveDateTime(iso: string | null | undefined): string {
  return iso ? iso.replace("T", " ").slice(0, 16) : "—";
}

export const CATEGORY_LABELS: Record<string, string> = {
  NEEDS_REVIEW: "Needs review",
  CURRENCY_MISMATCH: "Currency mismatch",
  MISSING_PAYMENT: "Missing payment",
  PAYMENT_FAILED: "Payment failed",
  PAYMENT_PENDING: "Payment pending",
  CANCELLED_BUT_CHARGED: "Cancelled but charged",
  REFUND_INCOMPLETE: "Refund incomplete",
  OVER_REFUNDED: "Over-refunded",
  REFUND_NOT_IN_ORDERS: "Refund not in orders",
  DUPLICATE_CHARGE: "Duplicate charge",
  OVERCHARGED: "Overcharged",
  UNDERCHARGED: "Undercharged",
  WITHIN_TOLERANCE: "Within tolerance",
  LATE_CHARGE: "Late charge",
  MATCHED: "Matched",
  ORPHAN_PAYMENT: "Orphan payment",
};

export const RECONCILED = ["MATCHED", "WITHIN_TOLERANCE", "LATE_CHARGE"];
export const DISPUTED = Object.keys(CATEGORY_LABELS).filter((c) => !RECONCILED.includes(c));

export const categoryLabel = (c: string) => CATEGORY_LABELS[c] ?? c;

export const DIRECTION_LABELS: Record<Direction, string> = {
  under_collected: "Under-collected",
  over_collected: "Over-collected",
  unverified: "Unverified",
};

export const SEVERITY_LABELS: Record<Severity, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
  none: "—",
};

export const flagLabel = (flag: string) => flag.replaceAll("_", " ");
