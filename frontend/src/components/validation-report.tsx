import { AlertTriangle, CheckCircle2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { FileReport } from "@/lib/types";

function FileSection({ report }: { report: FileReport }) {
  const issues = report.rejected.length + report.id_conflicts.length;
  return (
    <section className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium capitalize">{report.file}</h3>
        <Badge variant="secondary">{report.row_count} rows</Badge>
        <Badge variant="secondary">{report.accepted} accepted</Badge>
        {report.rejected_count > 0 && <Badge variant="destructive">{report.rejected_count} rejected</Badge>}
        {report.duplicate_rows_collapsed > 0 && <Badge variant="outline">{report.duplicate_rows_collapsed} exact duplicates merged</Badge>}
        {report.id_conflicts.length > 0 && <Badge variant="destructive">{report.id_conflicts.length} id conflicts</Badge>}
      </div>
      {issues === 0 ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <CheckCircle2 className="size-4" /> Every row was accepted.
        </p>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-24">Row</TableHead>
                <TableHead>Problem</TableHead>
                <TableHead className="w-40">Affects order</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {report.rejected.map((r) => (
                <TableRow key={`r${r.row_number}`}>
                  <TableCell className="font-mono">{r.row_number}</TableCell>
                  <TableCell className="whitespace-normal">{r.reasons.join("; ")}</TableCell>
                  <TableCell className="font-mono">{r.linked_order_key ?? <span className="text-muted-foreground">report only</span>}</TableCell>
                </TableRow>
              ))}
              {report.id_conflicts.map((c) => (
                <TableRow key={`c${c.key}`}>
                  <TableCell className="font-mono">{c.row_numbers.join(", ")}</TableCell>
                  <TableCell className="whitespace-normal">
                    Same id <span className="font-mono">{c.key}</span> with different content; none of these rows were used.
                  </TableCell>
                  <TableCell className="font-mono">{c.order_keys.join(", ") || "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}

export function ValidationReport({ orders, payments }: { orders: FileReport; payments: FileReport }) {
  const rejected = orders.rejected_count + payments.rejected_count;
  return (
    <div className="grid gap-5">
      {rejected > 0 && (
        <p className="flex items-center gap-2 text-sm">
          <AlertTriangle className="size-4 text-sev-medium" />
          {rejected} row{rejected === 1 ? " was" : "s were"} rejected and left out. Orders they point to are marked “Needs review”.
        </p>
      )}
      <FileSection report={orders} />
      <FileSection report={payments} />
    </div>
  );
}
