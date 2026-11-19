"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AlertCircle, ArrowRight, Info, Loader2, RotateCw } from "lucide-react";

import { checkFile, FileDrop } from "@/components/file-drop";
import { ValidationReport } from "@/components/validation-report";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError, errorMessage } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { HistoryRow, ImportRecord } from "@/lib/types";

type UploadResult = { import: ImportRecord; already_imported: boolean };
type Slot = "orders" | "payments";

export default function ImportsPage() {
  const [files, setFiles] = useState<Record<Slot, File | null>>({ orders: null, payments: null });
  const [fileErrors, setFileErrors] = useState<Record<Slot, string | null>>({ orders: null, payments: null });
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);

  const [history, setHistory] = useState<HistoryRow[] | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const loadHistory = useCallback(() => {
    setHistoryError(null);
    api<{ imports: HistoryRow[] }>("/imports")
      .then((r) => setHistory(r.imports))
      .catch((err) => setHistoryError(errorMessage(err)));
  }, []);

  useEffect(loadHistory, [loadHistory]);

  function choose(slot: Slot, file: File | null) {
    setFiles((f) => ({ ...f, [slot]: file }));
    setFileErrors((e) => ({ ...e, [slot]: file ? checkFile(file) : null }));
    setUploadError(null);
    setResult(null);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (uploading || !files.orders || !files.payments) return;
    setUploading(true);
    setUploadError(null);
    const form = new FormData();
    form.append("orders", files.orders);
    form.append("payments", files.payments);
    try {
      const r = await api<UploadResult>("/imports", { method: "POST", form });
      setResult(r);
      setFiles({ orders: null, payments: null });
      loadHistory();
    } catch (err) {
      const slot = err instanceof ApiError ? (err.details as { file?: Slot } | undefined)?.file : undefined;
      if (slot) setFileErrors((x) => ({ ...x, [slot]: errorMessage(err) }));
      else setUploadError(errorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  const ready = files.orders && files.payments && !fileErrors.orders && !fileErrors.payments;
  const validation = result?.import.summary.validation;
  const warnings = validation ? validation.orders.rejected_count + validation.payments.rejected_count + validation.orders.id_conflicts.length + validation.payments.id_conflicts.length : 0;

  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader>
          <CardTitle>New import</CardTitle>
          <CardDescription>Upload the orders export and the payment processor export together. CSV, up to 2 MB each.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="grid gap-4">
            <div className="grid gap-4 md:grid-cols-2">
              <FileDrop label="Orders file" hint="order_id, currency, net_amount, status …" file={files.orders} error={fileErrors.orders} disabled={uploading} onChange={(f) => choose("orders", f)} />
              <FileDrop label="Payments file" hint="transaction_ref, currency, amount, type, status …" file={files.payments} error={fileErrors.payments} disabled={uploading} onChange={(f) => choose("payments", f)} />
            </div>
            {uploadError && (
              <Alert variant="destructive">
                <AlertCircle />
                <AlertDescription>{uploadError}</AlertDescription>
              </Alert>
            )}
            <div>
              <Button type="submit" disabled={!ready || uploading}>
                {uploading && <Loader2 className="animate-spin" />}
                {uploading ? "Validating and reconciling…" : "Upload and reconcile"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {result && validation && (
        <Card>
          <CardHeader>
            <CardTitle>{result.already_imported ? "Already imported" : warnings ? "Imported with warnings" : "Imported"}</CardTitle>
            <CardDescription>
              {result.import.orders_filename} + {result.import.payments_filename}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-5">
            {result.already_imported && (
              <Alert>
                <Info />
                <AlertDescription>These exact files were imported before, so the existing results are shown. Nothing was duplicated.</AlertDescription>
              </Alert>
            )}
            {result.import.summary.metrics.totals.items === 0 && (
              <Alert>
                <Info />
                <AlertTitle>Nothing to reconcile</AlertTitle>
                <AlertDescription>The files were valid but contained no usable records.</AlertDescription>
              </Alert>
            )}
            <ValidationReport orders={validation.orders} payments={validation.payments} />
            <div>
              <Button asChild>
                <Link href={`/imports/${result.import.id}`}>
                  Open dashboard <ArrowRight />
                </Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Import history</CardTitle>
        </CardHeader>
        <CardContent>
          {historyError ? (
            <Alert variant="destructive">
              <AlertCircle />
              <AlertDescription className="flex flex-wrap items-center gap-3">
                {historyError}
                <Button size="sm" variant="outline" onClick={loadHistory}>
                  <RotateCw /> Retry
                </Button>
              </AlertDescription>
            </Alert>
          ) : history === null ? (
            <div className="grid gap-2">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-10" />
              ))}
            </div>
          ) : history.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">No imports yet. Upload your first pair of files above.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Imported</TableHead>
                  <TableHead>Files</TableHead>
                  <TableHead className="text-right">Orders</TableHead>
                  <TableHead className="text-right">Payments</TableHead>
                  <TableHead className="text-right">Needs review</TableHead>
                  <TableHead className="text-right">Rejected rows</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.map((h) => (
                  <TableRow key={h.id}>
                    <TableCell>{dateTime(h.created_at)}</TableCell>
                    <TableCell className="max-w-72 truncate" title={`${h.orders_filename} + ${h.payments_filename}`}>
                      {h.orders_filename} + {h.payments_filename}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{h.totals?.unique_orders ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">{h.totals?.payment_events ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">{h.totals?.needs_review ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">{h.rejected_rows}</TableCell>
                    <TableCell className="text-right">
                      <Button asChild variant="outline" size="sm">
                        <Link href={`/imports/${h.id}`}>Open</Link>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
