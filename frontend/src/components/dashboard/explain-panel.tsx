"use client";

import { useState } from "react";
import { AlertCircle, Info, Loader2, RotateCw, Sparkles } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, errorMessage } from "@/lib/api";
import type { Explanation } from "@/lib/types";

type Props<T> = {
  path: string;
  initial: Explanation<T> | null;
  buttonLabel: string;
  render: (output: T) => React.ReactNode;
};

/** Shared "ask the LLM" flow: idle → loading → result | fallback | error. Output is plain text. */
export function ExplainPanel<T>({ path, initial, buttonLabel, render }: Props<T>) {
  const [result, setResult] = useState<Explanation<T> | null>(initial);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (pending) return; // the disabled button also blocks double submits
    setPending(true);
    setError(null);
    try {
      const r = await api<{ explanation: Explanation<T> }>(path, { method: "POST" });
      setResult(r.explanation);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  }

  const canRun = !result || result.source === "fallback";

  return (
    <div className="grid gap-3">
      {canRun && (
        <div>
          <Button onClick={run} disabled={pending} variant={result ? "outline" : "default"}>
            {pending ? <Loader2 className="animate-spin" /> : result ? <RotateCw /> : <Sparkles />}
            {pending ? "Asking AI…" : result ? "Try AI again" : buttonLabel}
          </Button>
        </div>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertCircle />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {result && (
        <div className="grid gap-3 rounded-lg border bg-muted/30 p-4">
          {result.source === "fallback" ? (
            <Alert>
              <Info />
              <AlertDescription>AI is unavailable right now, so this is a rule-based explanation built from the evidence.</AlertDescription>
            </Alert>
          ) : (
            <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
              <Badge variant="secondary">
                <Sparkles /> AI generated
              </Badge>
              <span>
                {result.model} · prompt {result.prompt_version}
              </span>
            </div>
          )}
          {render(result.output)}
          <p className="text-xs text-muted-foreground">AI explains the result; it does not change it. Check the evidence before acting.</p>
        </div>
      )}
    </div>
  );
}
