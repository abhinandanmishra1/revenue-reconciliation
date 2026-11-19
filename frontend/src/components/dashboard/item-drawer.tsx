"use client";

import { useEffect, useState } from "react";
import { AlertCircle } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { categoryLabel, DIRECTION_LABELS, flagLabel, money, naiveDateTime } from "@/lib/format";
import type { Evidence, Explanation, Item, ItemExplanation } from "@/lib/types";

import { ExplainPanel } from "./explain-panel";
import { SeverityBadge } from "./severity-badge";

type Detail = { item: Item & { evidence: Evidence }; explanation: Explanation<ItemExplanation> | null };

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{children}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

export function ItemDrawer({ importId, itemId, onClose }: { importId: string; itemId: string | null; onClose: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!itemId) return;
    const ctrl = new AbortController();
    setDetail(null);
    setError(null);
    api<Detail>(`/imports/${importId}/items/${itemId}`, { signal: ctrl.signal })
      .then(setDetail)
      .catch((err) => (err as Error).name !== "AbortError" && setError(errorMessage(err)));
    return () => ctrl.abort();
  }, [importId, itemId]);

  const item = detail?.item;
  const ev = item?.evidence;
  const cur = item?.currency ?? null;

  return (
    <Sheet open={itemId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{item ? `${categoryLabel(item.category)} · ${item.order_key ?? item.item_key.replace(/^T:/, "payment ")}` : "Result details"}</SheetTitle>
          <SheetDescription>{item?.reason ?? "Loading evidence…"}</SheetDescription>
        </SheetHeader>
        <div className="grid gap-6 p-5 pt-2">
          {error && (
            <Alert variant="destructive">
              <AlertCircle />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {!item && !error && (
            <div className="grid gap-3">
              <Skeleton className="h-16" />
              <Skeleton className="h-32" />
            </div>
          )}
          {item && ev && (
            <>
              <dl className="grid grid-cols-2 gap-3 rounded-lg border p-4 sm:grid-cols-3">
                <Field label="Severity">
                  <SeverityBadge severity={item.severity} />
                </Field>
                <Field label="Expected">{money(item.expected_amount, cur)}</Field>
                <Field label="Collected (settled)">{money(item.actual_amount, cur)}</Field>
                <Field label="Difference">{money(item.difference, cur)}</Field>
                <Field label="At risk">
                  {money(item.amount_at_risk, cur)}
                  {item.risk_direction && <span className="block text-xs font-normal text-muted-foreground">{DIRECTION_LABELS[item.risk_direction]}</span>}
                </Field>
                {ev.excess_refund && Number(ev.excess_refund) > 0 && <Field label="Excess refund">{money(ev.excess_refund, cur)}</Field>}
                <Field label="Rule">
                  #{ev.rule.number ?? "—"} · rules {ev.rule.rules_version}
                </Field>
                <Field label="Tolerance">{money(ev.tolerance, cur)}</Field>
              </dl>

              {ev.review_reasons && ev.review_reasons.length > 0 && (
                <Section title="Why it needs review">
                  <ul className="list-disc pl-5 text-sm">
                    {ev.review_reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                </Section>
              )}

              <Section title="Order record">
                {ev.order ? (
                  <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
                    <Field label="Order id (as written)">
                      <span className="font-mono">{ev.order.order_id_raw}</span>
                    </Field>
                    <Field label="Source row">{ev.order.row_numbers.join(", ")}</Field>
                    <Field label="Status">{ev.order.status}</Field>
                    <Field label="Net amount">{money(ev.order.net_amount, ev.order.currency)}</Field>
                    <Field label="Gross / discount">
                      {money(ev.order.gross_amount, ev.order.currency)} / {money(ev.order.discount, ev.order.currency)}
                    </Field>
                    <Field label="Order date">{naiveDateTime(ev.order.order_date)}</Field>
                    <Field label="Customer">{ev.order.customer_email ?? "—"}</Field>
                  </dl>
                ) : (
                  <p className="text-sm text-muted-foreground">No order record for this key.</p>
                )}
              </Section>

              <Section title={`Payment events (${ev.events.length})`}>
                {ev.events.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No payment events reference this order.</p>
                ) : (
                  <div className="rounded-lg border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Reference</TableHead>
                          <TableHead>Row</TableHead>
                          <TableHead>Type</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead className="text-right">Amount</TableHead>
                          <TableHead>Processed</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {ev.events.map((e) => (
                          <TableRow key={e.transaction_ref}>
                            <TableCell className="font-mono text-xs">{e.transaction_ref}</TableCell>
                            <TableCell>{e.row_numbers.join(", ")}</TableCell>
                            <TableCell>{e.type}</TableCell>
                            <TableCell>{e.status}</TableCell>
                            <TableCell className="text-right tabular-nums">{money(e.amount, e.currency)}</TableCell>
                            <TableCell>{naiveDateTime(e.processed_at)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </Section>

              {(item.dq_flags.length > 0 || (ev.timing_breaches?.length ?? 0) > 0) && (
                <Section title="Data-quality notes">
                  <div className="flex flex-wrap gap-1.5">
                    {item.dq_flags.map((f) => (
                      <Badge key={f} variant="outline">
                        {flagLabel(f)}
                      </Badge>
                    ))}
                  </div>
                  {ev.timing_breaches?.map((t) => (
                    <p key={t.transaction_ref} className="text-sm text-muted-foreground">
                      {t.transaction_ref} settled {t.hours_after_order} h after the order.
                    </p>
                  ))}
                </Section>
              )}

              <Section title="Explanation">
                <ExplainPanel<ItemExplanation>
                  key={item.id}
                  path={`/imports/${importId}/items/${item.id}/explain`}
                  initial={detail.explanation}
                  buttonLabel="Explain with AI"
                  render={(o) => (
                    <div className="grid gap-2 text-sm">
                      <p>{o.summary}</p>
                      <p>
                        <span className="font-medium">Likely cause: </span>
                        {o.likely_cause}
                      </p>
                      <div>
                        <p className="font-medium">Recommended actions</p>
                        <ul className="list-disc pl-5">
                          {o.recommended_actions.map((a) => (
                            <li key={a}>{a}</li>
                          ))}
                        </ul>
                      </div>
                      <p className="text-xs text-muted-foreground">Confidence: {o.confidence}</p>
                    </div>
                  )}
                />
              </Section>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
