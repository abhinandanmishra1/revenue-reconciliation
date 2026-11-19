"use client";

import { cn } from "@/lib/utils";
import { DISPUTED, money, RECONCILED } from "@/lib/format";
import type { CurrencyMetrics, Totals } from "@/lib/types";

import type { Filters } from "./filters";

type Props = {
  currency: string;
  metrics: CurrencyMetrics;
  totals: Totals;
  onFilter: (patch: Partial<Filters>) => void;
};

function Tile({ label, value, sub, onClick, children }: { label: string; value?: string; sub?: React.ReactNode; onClick?: () => void; children?: React.ReactNode }) {
  const Comp = onClick ? "button" : "div";
  return (
    <Comp
      onClick={onClick}
      className={cn(
        "flex flex-col gap-1 rounded-xl border bg-card p-4 text-left shadow-xs",
        onClick && "cursor-pointer transition-colors hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
      )}
    >
      <span className="text-sm text-muted-foreground">{label}</span>
      {value && <span className="text-2xl font-semibold tabular-nums">{value}</span>}
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
      {children}
    </Comp>
  );
}

function RiskLine({ label, value, note, onClick }: { label: string; value: string; note?: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex items-baseline justify-between gap-3 rounded px-1 py-0.5 text-left hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none">
      <span className="text-sm">
        {label}
        {note && <span className="block text-xs text-muted-foreground">{note}</span>}
      </span>
      <span className="font-semibold tabular-nums">{value}</span>
    </button>
  );
}

export function KpiRow({ currency, metrics, totals, onFilter }: Props) {
  const cur = currency;
  const r = metrics.at_risk;
  const pending = Number(r.under_collected_pending) > 0 ? `incl. ${money(r.under_collected_pending, cur)} pending` : undefined;
  const byStatus = totals.payment_events_by_type_status;
  const settled = (byStatus.charge_settled ?? 0) + (byStatus.refund_settled ?? 0);

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <Tile label={`Orders (${cur})`} value={String(metrics.orders)} sub={`${totals.unique_orders} unique of ${totals.order_rows} rows, all currencies`} />
      <Tile
        label="Payments"
        value={String(totals.payment_events)}
        sub={
          <>
            {settled} settled · {(byStatus.charge_pending ?? 0) + (byStatus.refund_pending ?? 0)} pending · {(byStatus.charge_failed ?? 0) + (byStatus.refund_failed ?? 0)} failed
            <br />
            {money(metrics.settled_charge_value, cur)} settled charges
          </>
        }
      />
      <Tile
        label="Reconciled"
        value={money(metrics.reconciled_value, cur)}
        sub={`${metrics.reconciled_orders} orders`}
        onClick={() => onFilter({ category: RECONCILED, currency: cur, source: `Reconciled (${cur})` })}
      />
      <Tile
        label="In dispute"
        value={money(metrics.disputed_value, cur)}
        sub="Order value affected by a discrepancy"
        onClick={() => onFilter({ category: DISPUTED, currency: cur, source: `In dispute (${cur})` })}
      />
      <Tile label="Money at risk">
        <div className="mt-1 grid gap-0.5">
          <RiskLine label="Under-collected" note={pending} value={money(r.under_collected, cur)} onClick={() => onFilter({ direction: ["under_collected"], currency: cur, source: `Under-collected (${cur})` })} />
          <RiskLine label="Over-collected" value={money(r.over_collected, cur)} onClick={() => onFilter({ direction: ["over_collected"], currency: cur, source: `Over-collected (${cur})` })} />
          <RiskLine label="Unverified" value={money(r.unverified, cur)} onClick={() => onFilter({ direction: ["unverified"], category: ["CURRENCY_MISMATCH"], currency: cur, source: `Unverified (${cur})` })} />
          {totals.needs_review > 0 && (
            <RiskLine label="Needs review" note="exposure unknown, all currencies" value={String(totals.needs_review)} onClick={() => onFilter({ category: ["NEEDS_REVIEW"], source: "Needs review" })} />
          )}
        </div>
      </Tile>
    </div>
  );
}
