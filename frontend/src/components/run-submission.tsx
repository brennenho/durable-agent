"use client";

import { useRouter } from "next/navigation";
import { ArrowUp, LoaderCircle } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import { ApiError, requestJson, type ConversationSummary, type SubmitRunRequest } from "../lib/api";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

export function RunSubmission() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<SubmitRunRequest | null>(null);
  const submitting = useRef(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    if (!prompt.trim()) {
      setError("Enter a task for the agent.");
      return;
    }
    if (pending.current?.prompt !== prompt) {
      pending.current = { submission_key: crypto.randomUUID(), prompt };
    }
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      const saved = await requestJson<ConversationSummary>("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pending.current),
      });
      router.push(`/chats/${encodeURIComponent(saved.id)}`);
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Could not confirm submission. Retry to check the same request.",
      );
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} aria-busy={busy} aria-label="New chat" className="chat-composer">
      <label htmlFor="prompt" className="sr-only">
        Message
      </label>
      <Textarea
        className="min-h-24 max-h-64 resize-y border-0 bg-transparent p-0 text-base shadow-none focus-visible:ring-0 md:text-sm"
        id="prompt"
        name="prompt"
        rows={3}
        required
        disabled={busy}
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
        }}
        aria-describedby={error ? "submission-error" : "prompt-help"}
        placeholder="Ask the agent to write, edit, or build something…"
      />
      {error && (
        <p id="submission-error" role="alert">
          {error}
        </p>
      )}
      <div className="mt-3 flex items-center justify-between gap-4">
        <p id="prompt-help" className="m-0 text-xs text-muted-foreground">
          Enter to send · Shift + Enter for a new line
        </p>
        <Button
          type="submit"
          size="icon-sm"
          disabled={busy || !prompt.trim()}
          aria-label={busy ? "Sending…" : error ? "Try again" : "Send message"}
        >
          {busy ? (
            <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" />
          ) : (
            <ArrowUp aria-hidden="true" />
          )}
        </Button>
      </div>
    </form>
  );
}
