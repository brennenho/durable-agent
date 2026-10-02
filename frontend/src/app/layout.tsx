import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AppShell } from "../components/app-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "Applied Agent",
  description: "An agent for coding and document tasks.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{process.env.CRASH_TEST_URL ? children : <AppShell>{children}</AppShell>}</body>
    </html>
  );
}
