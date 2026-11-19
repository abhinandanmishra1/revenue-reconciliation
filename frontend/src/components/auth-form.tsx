"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { AlertCircle, Loader2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError, errorMessage, safeNext } from "@/lib/api";

type Mode = "login" | "signup";

const COPY = {
  login: { title: "Log in", action: "Log in", alt: "No account yet?", altLink: "/signup", altText: "Sign up" },
  signup: { title: "Create an account", action: "Sign up", alt: "Already have an account?", altLink: "/login", altText: "Log in" },
};

export function AuthForm({ mode }: { mode: Mode }) {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const copy = COPY[mode];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    setError(null);
    if (mode === "signup" && new TextEncoder().encode(password).length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setPending(true);
    try {
      await api(`/auth/${mode}`, { method: "POST", body: { email, password } });
      router.replace(safeNext(params.get("next")));
      router.refresh();
    } catch (err) {
      const fieldErrors = err instanceof ApiError && Array.isArray(err.details) ? (err.details as { message: string }[]) : [];
      setError(fieldErrors.length ? fieldErrors.map((d) => d.message.replace(/^Value error, /, "")).join(". ") : errorMessage(err));
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">{copy.title}</CardTitle>
          <CardDescription>Revenue Reconciliation</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="grid gap-4" noValidate>
            {params.has("expired") && !error && (
              <Alert>
                <AlertCircle />
                <AlertDescription>Your session has ended. Please log in again.</AlertDescription>
              </Alert>
            )}
            {error && (
              <Alert variant="destructive">
                <AlertCircle />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <div className="grid gap-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              {mode === "signup" && <p className="text-xs text-muted-foreground">8 to 72 characters.</p>}
            </div>
            <Button type="submit" disabled={pending || !email || !password}>
              {pending && <Loader2 className="animate-spin" />}
              {copy.action}
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              {copy.alt}{" "}
              <Link href={copy.altLink} className="font-medium text-foreground underline-offset-4 hover:underline">
                {copy.altText}
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
