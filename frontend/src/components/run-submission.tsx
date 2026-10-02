"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { ApiError, requestJson, type ConversationSummary, type SubmitRunRequest } from "../lib/api";
import { MessageComposer } from "./message-composer";

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
    <MessageComposer
      label="New chat"
      prompt={prompt}
      onPromptChange={setPrompt}
      onSubmit={submit}
      busy={busy}
      error={error}
    />
  );
}
