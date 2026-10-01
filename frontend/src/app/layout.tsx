import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import { Terminal } from "lucide-react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Durable Agent",
  description: "Start and inspect durable agent runs.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="app-header">
          <Link
            href="/"
            className="flex items-center gap-2.5 font-semibold tracking-tight text-foreground"
          >
            <Terminal aria-hidden="true" className="size-4" />
            Durable Agent
          </Link>
          <span className="text-xs text-muted-foreground">Workspace</span>
        </header>
        {children}
      </body>
    </html>
  );
}
