"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, requestJson, type ConversationSummary, type SubmitRunRequest } from "../lib/api";
import { MessageComposer } from "./message-composer";

export function RunSubmission() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<SubmitRunRequest | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (controller.current) return;
    if (!prompt.trim()) {
      setError("Enter a task for the agent.");
      return;
    }
    if (pending.current?.prompt !== prompt) {
      pending.current = { submission_key: crypto.randomUUID(), prompt };
    }
    const requestController = new AbortController();
    controller.current = requestController;
    setBusy(true);
    setError(null);
    try {
      const saved = await requestJson<ConversationSummary>("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pending.current),
        signal: requestController.signal,
      });
      if (!requestController.signal.aborted) router.push(`/chats/${encodeURIComponent(saved.id)}`);
    } catch (cause) {
      if (requestController.signal.aborted) return;
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Could not confirm submission. Retry to check the same request.",
      );
    } finally {
      controller.current = null;
      if (!requestController.signal.aborted) setBusy(false);
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
