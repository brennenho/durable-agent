"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { PanelLeft, SquarePen, X } from "lucide-react";
import { Dialog } from "radix-ui";
import { useEffect, useState, type ReactNode } from "react";
import { requestJson, type ConversationSummary } from "../lib/api";
import { Button } from "./ui/button";

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    requestJson<ConversationSummary[]>("/api/conversations", { signal: controller.signal })
      .then((saved) => {
        if (controller.signal.aborted) return;
        setConversations(saved);
        setError(null);
      })
      .catch((cause) => {
        if (!controller.signal.aborted)
          setError(cause instanceof Error ? cause.message : "Could not load previous chats.");
      });
    return () => controller.abort();
  }, [pathname, retry]);

  const navigation = (
    <nav aria-label="Previous chats" className="flex h-full min-h-0 flex-col gap-6 p-3">
      <Link
        href="/"
        onClick={() => setOpen(false)}
        className="flex items-center gap-2.5 px-3 py-3 font-semibold tracking-tight"
      >
        <span aria-hidden="true" className="size-2.5 shrink-0 rounded-xs bg-primary" />
        Applied Agent
      </Link>
      <Button asChild variant="outline" className="justify-start bg-card shadow-none">
        <Link href="/" onClick={() => setOpen(false)}>
          <SquarePen aria-hidden="true" />
          New chat
        </Link>
      </Button>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <p className="m-0 px-3 pb-3 text-xs text-muted-foreground">Recent chats</p>
        {!conversations && !error && (
          <p className="px-3 text-xs text-muted-foreground">Loading chats…</p>
        )}
        {error && (
          <div className="px-3">
            <p role="alert" aria-label="Chat list error" className="text-xs">
              {error}
            </p>
            <Button variant="outline" size="sm" onClick={() => setRetry((value) => value + 1)}>
              Retry chats
            </Button>
          </div>
        )}
        {conversations?.length === 0 && (
          <p className="px-3 text-xs text-muted-foreground">Your chats will appear here.</p>
        )}
        <ul className="m-0 space-y-1 p-0">
          {conversations?.map((conversation) => {
            const href = `/chats/${conversation.id}`;
            return (
              <li key={conversation.id}>
                <Link
                  href={href}
                  onClick={() => setOpen(false)}
                  title={conversation.title}
                  aria-current={pathname === href ? "page" : undefined}
                  className="block truncate rounded-md border-l-2 border-transparent px-3 py-2.5 hover:bg-accent aria-[current=page]:border-l-primary aria-[current=page]:bg-primary/8"
                >
                  {conversation.title}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    </nav>
  );

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[224px_minmax(0,1fr)]">
      <aside className="sticky top-0 hidden h-dvh bg-muted/50 md:block">{navigation}</aside>
      <div className="min-w-0">
        <header className="flex h-14 items-center gap-3 border-b px-3 md:hidden">
          <Dialog.Root open={open} onOpenChange={setOpen}>
            <Dialog.Trigger asChild>
              <Button variant="ghost" size="icon" aria-label="Open chats">
                <PanelLeft />
              </Button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="fixed inset-0 z-40 bg-foreground/20" />
              <Dialog.Content
                aria-describedby={undefined}
                className="fixed inset-y-0 left-0 z-50 w-72 max-w-[85vw] bg-background"
              >
                <Dialog.Title className="sr-only">Previous chats</Dialog.Title>
                <Dialog.Close asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Close chats"
                    className="absolute top-4 right-3"
                  >
                    <X />
                  </Button>
                </Dialog.Close>
                {navigation}
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
          <span className="flex items-center gap-2.5 font-medium">
            <span aria-hidden="true" className="size-2.5 shrink-0 rounded-xs bg-primary" />
            Applied Agent
          </span>
        </header>
        {children}
      </div>
    </div>
  );
}
