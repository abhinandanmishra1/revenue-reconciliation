import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Revenue Reconciliation",
  description: "Reconcile order records with payment processor records.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans">{children}</body>
    </html>
  );
}
