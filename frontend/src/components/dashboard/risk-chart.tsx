"use client";

import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { categoryLabel, money } from "@/lib/format";
import type { CurrencyMetrics } from "@/lib/types";

type Props = { currency: string; metrics: CurrencyMetrics; active: string[]; onSelect: (category: string) => void };

export function RiskChart({ currency, metrics, active, onSelect }: Props) {
  const data = Object.entries(metrics.categories)
    .map(([category, v]) => ({ category, label: categoryLabel(category), value: Number(v.amount_at_risk), raw: v.amount_at_risk, count: v.count }))
    .filter((d) => d.value > 0)
    .sort((a, b) => b.value - a.value);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Money at risk by category ({currency})</CardTitle>
        <CardDescription>Click a bar to filter the table.</CardDescription>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No money at risk in {currency}.</p>
        ) : (
          <div style={{ height: 40 + data.length * 36 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} layout="vertical" margin={{ top: 0, right: 96, bottom: 0, left: 0 }} barCategoryGap={6}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="label" width={160} tickLine={false} axisLine={false} tick={{ fill: "var(--muted-foreground)", fontSize: 12 }} />
                <Tooltip
                  cursor={{ fill: "var(--muted)" }}
                  contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--popover-foreground)" }}
                  formatter={(_v, _n, item) => [`${money(item.payload.raw, currency)} · ${item.payload.count} item${item.payload.count === 1 ? "" : "s"}`, "At risk"]}
                />
                <Bar
                  dataKey="value"
                  fill="var(--chart-bar)"
                  radius={[0, 4, 4, 0]}
                  className="cursor-pointer"
                  onClick={(d) => onSelect((d as unknown as { category: string }).category)}
                  isAnimationActive={false}
                  fillOpacity={1}
                  shape={(props: unknown) => {
                    const p = props as { x: number; y: number; width: number; height: number; payload: { category: string } };
                    const dim = active.length > 0 && !active.includes(p.payload.category);
                    return <rect x={p.x} y={p.y} width={Math.max(p.width, 2)} height={p.height} rx={4} fill="var(--chart-bar)" opacity={dim ? 0.35 : 1} />;
                  }}
                >
                  <LabelList dataKey="raw" position="right" formatter={(v: unknown) => money(String(v), currency)} style={{ fill: "var(--foreground)", fontSize: 12 }} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
