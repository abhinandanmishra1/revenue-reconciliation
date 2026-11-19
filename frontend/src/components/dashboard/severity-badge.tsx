import { cn } from "@/lib/utils";
import { SEVERITY_LABELS } from "@/lib/format";
import type { Severity } from "@/lib/types";

const DOT: Record<Severity, string> = {
  high: "bg-sev-high",
  medium: "bg-sev-medium",
  low: "bg-sev-low",
  info: "bg-muted-foreground",
  none: "bg-border",
};

/** Colour is never the only signal: the label is always shown. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <span className={cn("size-2 rounded-full", DOT[severity])} aria-hidden />
      {SEVERITY_LABELS[severity]}
    </span>
  );
}
