"use client";

import { useEffect, useMemo, useState } from "react";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable, type SortingState } from "@tanstack/react-table";
import { AlertCircle, ArrowDown, ArrowUp, ArrowUpDown, RotateCw, Search, X } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { CATEGORY_LABELS, categoryLabel, DIRECTION_LABELS, money, SEVERITY_LABELS } from "@/lib/format";
import type { Item, ItemPage } from "@/lib/types";

import { DEFAULT_FILTERS, PAGE_SIZE, toQuery, type Filters, type SortField } from "./filters";
import { SeverityBadge } from "./severity-badge";

const col = createColumnHelper<Item>();

type Props = {
  importId: string;
  currencies: string[];
  filters: Filters;
  onChange: (patch: Partial<Filters>) => void;
  onOpen: (itemId: string) => void;
};

export function ItemsTable({ importId, currencies, filters, onChange, onOpen }: Props) {
  const [page, setPage] = useState<ItemPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState(filters.q);
  const [reload, setReload] = useState(0);
  const query = toQuery(filters);

  useEffect(() => setSearch(filters.q), [filters.q]);

  // Debounce typing into the search box.
  useEffect(() => {
    if (search === filters.q) return;
    const t = setTimeout(() => onChange({ q: search, page: 1 }), 300);
    return () => clearTimeout(t);
  }, [search, filters.q, onChange]);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    api<ItemPage>(`/imports/${importId}/items?${query}`, { signal: ctrl.signal })
      .then((r) => setPage(r))
      .catch((err) => {
        if ((err as Error).name !== "AbortError") setError(errorMessage(err));
      })
      .finally(() => !ctrl.signal.aborted && setLoading(false));
    return () => ctrl.abort();
  }, [importId, query, reload]);

  const columns = useMemo(
    () => [
      col.accessor("severity", { header: "Severity", enableSorting: false, cell: (c) => <SeverityBadge severity={c.getValue()} /> }),
      col.accessor("category", { header: "Category", cell: (c) => categoryLabel(c.getValue()) }),
      col.accessor("order_key", {
        header: "Order / reference",
        cell: (c) => <span className="font-mono text-xs">{c.getValue() ?? c.row.original.item_key.replace(/^T:/, "payment ")}</span>,
      }),
      col.accessor("expected_amount", { header: "Expected", enableSorting: false, cell: (c) => <Num>{money(c.getValue(), c.row.original.currency)}</Num> }),
      col.accessor("actual_amount", { header: "Collected", enableSorting: false, cell: (c) => <Num>{money(c.getValue(), c.row.original.currency)}</Num> }),
      col.accessor("difference", { header: "Difference", cell: (c) => <Num>{money(c.getValue(), c.row.original.currency)}</Num> }),
      col.accessor("amount_at_risk", {
        header: "At risk",
        cell: (c) => (
          <div className="text-right">
            <div className="font-medium tabular-nums">{money(c.getValue(), c.row.original.currency)}</div>
            {c.row.original.risk_direction && <div className="text-xs text-muted-foreground">{DIRECTION_LABELS[c.row.original.risk_direction]}</div>}
          </div>
        ),
      }),
      col.accessor("dq_flags", {
        header: "Data quality",
        enableSorting: false,
        cell: (c) => (c.getValue().length ? <Badge variant="outline">{c.getValue().length} flag{c.getValue().length === 1 ? "" : "s"}</Badge> : null),
      }),
    ],
    [],
  );

  const sorting: SortingState = filters.sort === "priority" ? [] : [{ id: filters.sort, desc: filters.order === "desc" }];
  const table = useReactTable({
    data: page?.items ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
    state: { sorting },
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting) : updater;
      if (next.length === 0) onChange({ sort: "priority", order: "desc", page: 1 });
      else onChange({ sort: next[0].id as SortField, order: next[0].desc ? "desc" : "asc", page: 1 });
    },
  });

  const filtered = JSON.stringify({ ...filters, page: 1 }) !== JSON.stringify({ ...DEFAULT_FILTERS });
  const pages = page ? Math.max(1, Math.ceil(page.total / PAGE_SIZE)) : 1;

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-64">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input aria-label="Search" placeholder="Order id, transaction ref, email" value={search} onChange={(e) => setSearch(e.target.value)} className="pl-8" maxLength={100} />
        </div>
        <NativeSelect
          aria-label="Category"
          value={filters.category.length === 1 ? filters.category[0] : filters.category.length > 1 ? "__many" : ""}
          onChange={(e) => onChange({ category: e.target.value ? [e.target.value] : [], page: 1, source: null })}
        >
          <option value="">All except matched</option>
          {filters.category.length > 1 && <option value="__many">{filters.category.length} categories</option>}
          {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect aria-label="Severity" value={filters.severity} onChange={(e) => onChange({ severity: e.target.value, page: 1 })}>
          <option value="">Any severity</option>
          {(["high", "medium", "low", "info"] as const).map((s) => (
            <option key={s} value={s}>
              {SEVERITY_LABELS[s]}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect aria-label="Direction" value={filters.direction[0] ?? ""} onChange={(e) => onChange({ direction: e.target.value ? [e.target.value] : [], page: 1, source: null })}>
          <option value="">Any direction</option>
          {Object.entries(DIRECTION_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </NativeSelect>
        {currencies.length > 1 && (
          <NativeSelect aria-label="Currency" value={filters.currency} onChange={(e) => onChange({ currency: e.target.value, page: 1 })}>
            <option value="">Any currency</option>
            {currencies.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </NativeSelect>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={filters.has_dq} onChange={(e) => onChange({ has_dq: e.target.checked, page: 1 })} className="size-4" />
          Data-quality flags only
        </label>
        {filtered && (
          <Button variant="ghost" size="sm" onClick={() => onChange({ ...DEFAULT_FILTERS })}>
            <X /> Clear{filters.source ? `: ${filters.source}` : " filters"}
          </Button>
        )}
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertCircle />
          <AlertDescription className="flex flex-wrap items-center gap-3">
            {error}
            <Button size="sm" variant="outline" onClick={() => setReload((n) => n + 1)}>
              <RotateCw /> Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : (
        <div className="rounded-xl border bg-card">
          <Table>
            <TableHeader>
              {table.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id}>
                  {hg.headers.map((h) => {
                    const sorted = h.column.getIsSorted();
                    const right = ["expected_amount", "actual_amount", "difference", "amount_at_risk"].includes(h.column.id);
                    return (
                      <TableHead key={h.id} className={right ? "text-right" : undefined} aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}>
                        {h.column.getCanSort() ? (
                          <button className="inline-flex items-center gap-1 hover:text-foreground" onClick={h.column.getToggleSortingHandler()}>
                            {flexRender(h.column.columnDef.header, h.getContext())}
                            {sorted === "asc" ? <ArrowUp className="size-3" /> : sorted === "desc" ? <ArrowDown className="size-3" /> : <ArrowUpDown className="size-3 opacity-40" />}
                          </button>
                        ) : (
                          flexRender(h.column.columnDef.header, h.getContext())
                        )}
                      </TableHead>
                    );
                  })}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {loading && !page
                ? Array.from({ length: 6 }).map((_, i) => (
                    <TableRow key={i}>
                      <TableCell colSpan={columns.length}>
                        <Skeleton className="h-6" />
                      </TableCell>
                    </TableRow>
                  ))
                : table.getRowModel().rows.map((row) => (
                    <TableRow
                      key={row.id}
                      tabIndex={0}
                      className={loading ? "cursor-pointer opacity-60" : "cursor-pointer"}
                      onClick={() => onOpen(row.original.id)}
                      onKeyDown={(e) => e.key === "Enter" && onOpen(row.original.id)}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                      ))}
                    </TableRow>
                  ))}
              {!loading && page?.items.length === 0 && (
                <TableRow>
                  <TableCell colSpan={columns.length} className="py-10 text-center text-muted-foreground">
                    {filtered ? "No results match these filters." : "Nothing to look at: every order matched."}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}

      {page && page.total > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            {page.total} result{page.total === 1 ? "" : "s"}
            {filters.sort === "priority" && " · sorted by severity, then amount at risk"}
          </span>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={filters.page <= 1 || loading} onClick={() => onChange({ page: filters.page - 1 })}>
              Previous
            </Button>
            <span>
              Page {filters.page} of {pages}
            </span>
            <Button variant="outline" size="sm" disabled={filters.page >= pages || loading} onClick={() => onChange({ page: filters.page + 1 })}>
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function Num({ children }: { children: React.ReactNode }) {
  return <div className="text-right tabular-nums">{children}</div>;
}
