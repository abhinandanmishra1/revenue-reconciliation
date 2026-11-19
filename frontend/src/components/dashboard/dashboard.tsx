"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowLeft, Info, RotateCw } from "lucide-react";

import { ValidationReport } from "@/components/validation-report";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, ApiError, errorMessage } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Explanation, ImportRecord, SummaryExplanation } from "@/lib/types";

import { ExplainPanel } from "./explain-panel";
import { DEFAULT_FILTERS, type Filters } from "./filters";
import { ItemDrawer } from "./item-drawer";
import { ItemsTable } from "./items-table";
import { KpiRow } from "./kpi-row";
import { RiskChart } from "./risk-chart";

type Detail = { import: ImportRecord; summary_explanation: Explanation<SummaryExplanation> | null };

export function Dashboard({ importId }: { importId: string }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [tab, setTab] = useState("discrepancies");
  const [openItem, setOpenItem] = useState<string | null>(null);
  const [currency, setCurrency] = useState<string>("");

  const load = useCallback(() => {
    setError(null);
    api<Detail>(`/imports/${importId}`)
      .then(setDetail)
      .catch((err) => setError(err));
  }, [importId]);
  useEffect(load, [load]);

  const byCurrency = useMemo(() => detail?.import.summary.metrics.by_currency ?? {}, [detail]);
  // Largest currency first; "unknown" holds only review items with no currency.
  const currencies = useMemo(
    () => Object.keys(byCurrency).filter((c) => c !== "unknown").sort((a, b) => byCurrency[b].orders - byCurrency[a].orders || a.localeCompare(b)),
    [byCurrency],
  );
  const activeCurrency = currency || currencies[0] || "";

  const update = useCallback((patch: Partial<Filters>) => setFilters((f) => ({ ...f, page: 1, ...patch })), []);
  const applyFilter = useCallback((patch: Partial<Filters>) => {
    // KPI and chart clicks replace the current filter so the table shows exactly the rows behind the number.
    setFilters({ ...DEFAULT_FILTERS, ...patch });
    setTab("discrepancies");
  }, []);

  if (error) {
    const missing = error instanceof ApiError && (error.status === 404 || error.status === 422);
    return (
      <div className="grid gap-4">
        <BackLink />
        <Alert variant="destructive">
          <AlertCircle />
          <AlertTitle>{missing ? "Import not found" : "Could not load this import"}</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center gap-3">
            {missing ? "It may have been removed, or it belongs to another account." : errorMessage(error)}
            {!missing && (
              <Button size="sm" variant="outline" onClick={load}>
                <RotateCw /> Retry
              </Button>
            )}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-8 w-64" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  const { summary } = detail.import;
  const validation = summary.validation;
  const totals = summary.metrics.totals;
  const rejected = validation.orders.rejected_count + validation.payments.rejected_count;
  const metrics = byCurrency[activeCurrency];

  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid gap-1">
          <BackLink />
          <h1 className="text-xl font-semibold">
            {detail.import.orders_filename} + {detail.import.payments_filename}
          </h1>
          <p className="text-sm text-muted-foreground">
            Imported {dateTime(detail.import.created_at)} · rules {detail.import.rules_version}
          </p>
        </div>
        {currencies.length > 1 && (
          <label className="flex items-center gap-2 text-sm">
            Currency
            <NativeSelect aria-label="Dashboard currency" value={activeCurrency} onChange={(e) => setCurrency(e.target.value)}>
              {currencies.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </NativeSelect>
          </label>
        )}
      </div>

      {rejected > 0 && (
        <Alert>
          <Info />
          <AlertDescription className="flex flex-wrap items-center gap-2">
            {rejected} source row{rejected === 1 ? " was" : "s were"} rejected during import.
            <Button variant="link" className="h-auto p-0" onClick={() => setTab("quality")}>
              See the data-quality report
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {totals.items === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Nothing to reconcile</CardTitle>
            <CardDescription>This import has no accepted orders or payments. Check the data-quality report and upload corrected files.</CardDescription>
          </CardHeader>
          <CardContent>
            <ValidationReport orders={validation.orders} payments={validation.payments} />
          </CardContent>
        </Card>
      ) : (
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="discrepancies">Discrepancies</TabsTrigger>
            <TabsTrigger value="quality">Data quality</TabsTrigger>
            <TabsTrigger value="summary">AI summary</TabsTrigger>
          </TabsList>

          <TabsContent value="discrepancies" className="grid gap-5">
            {metrics && <KpiRow currency={activeCurrency} metrics={metrics} totals={totals} onFilter={applyFilter} />}
            {metrics && (
              <RiskChart
                currency={activeCurrency}
                metrics={metrics}
                active={filters.source?.startsWith("Chart") ? filters.category : []}
                onSelect={(category) => applyFilter({ category: [category], currency: activeCurrency, source: `Chart: ${category.toLowerCase().replaceAll("_", " ")}` })}
              />
            )}
            <section className="grid gap-3">
              <h2 className="text-lg font-semibold">Look at these first</h2>
              <ItemsTable importId={importId} currencies={currencies} filters={filters} onChange={update} onOpen={setOpenItem} />
            </section>
          </TabsContent>

          <TabsContent value="quality">
            <Card>
              <CardHeader>
                <CardTitle>Data quality</CardTitle>
                <CardDescription>Rows that could not be used, and records that were merged or flagged.</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-5">
                <ValidationReport orders={validation.orders} payments={validation.payments} />
                <div>
                  <Button variant="outline" onClick={() => applyFilter({ has_dq: true, source: "Data-quality flags" })}>
                    Show results with data-quality flags
                  </Button>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="summary">
            <Card>
              <CardHeader>
                <CardTitle>AI summary</CardTitle>
                <CardDescription>A plain-language overview of this import, generated from the computed metrics and the top 20 results.</CardDescription>
              </CardHeader>
              <CardContent>
                <ExplainPanel<SummaryExplanation>
                  path={`/imports/${importId}/summary/explain`}
                  initial={detail.summary_explanation}
                  buttonLabel="Summarise with AI"
                  render={(o) => (
                    <div className="grid gap-3 text-sm">
                      <p className="font-medium">{o.headline}</p>
                      <div>
                        <p className="font-medium">Top issues</p>
                        <ul className="list-disc pl-5">
                          {o.top_issues.map((t) => (
                            <li key={t}>{t}</li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <p className="font-medium">What to fix first</p>
                        <ol className="list-decimal pl-5">
                          {o.recommended_priorities.map((t) => (
                            <li key={t}>{t}</li>
                          ))}
                        </ol>
                      </div>
                    </div>
                  )}
                />
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      )}

      <ItemDrawer importId={importId} itemId={openItem} onClose={() => setOpenItem(null)} />
    </div>
  );
}

function BackLink() {
  return (
    <Link href="/imports" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
      <ArrowLeft className="size-4" /> Imports
    </Link>
  );
}
