"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LogOut } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

export function AppHeader() {
  const [user, setUser] = useState<User | null>(null);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    api<{ user: User }>("/auth/me").then((r) => setUser(r.user)).catch(() => undefined);
  }, []);

  async function logout() {
    setLeaving(true);
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    window.location.assign("/login");
  }

  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-4">
        <Link href="/imports" className="font-semibold">
          Revenue Reconciliation
        </Link>
        <div className="flex items-center gap-3 text-sm">
          <span className="hidden text-muted-foreground sm:inline">{user?.email}</span>
          <Button variant="ghost" size="sm" onClick={logout} disabled={leaving}>
            <LogOut /> Log out
          </Button>
        </div>
      </div>
    </header>
  );
}
