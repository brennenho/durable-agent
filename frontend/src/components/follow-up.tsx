"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ApiError,
  requestJson,
  type ConversationSummary,
  type RunSummary,
  type SubmitFollowUpRequest,
} from "../lib/api";
import { MessageComposer } from "./message-composer";

export function FollowUp({
  conversation,
  blocked,
  refresh,
}: {
  conversation: ConversationSummary;
  blocked: boolean;
  refresh: () => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const pending = useRef<SubmitFollowUpRequest | null>(null);
  const controller = useRef<AbortController | null>(null);
  const active = conversation.status !== "succeeded" && conversation.status !== "failed";
  const disabled = !uncertain && (active || blocked);
  useEffect(() => () => controller.current?.abort(), []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (controller.current || disabled || !prompt.trim()) return;
    // Keep the original predecessor and key until the server resolves this request.
    pending.current ??= {
      submission_key: crypto.randomUUID(),
      prompt,
      after_run_id: conversation.latest_run_id,
    };
    const requestController = new AbortController();
    controller.current = requestController;
    setBusy(true);
    setError(null);
    try {
      await requestJson<RunSummary>(
        `/api/conversations/${encodeURIComponent(conversation.id)}/runs`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(pending.current),
          signal: requestController.signal,
        },
      );
      if (requestController.signal.aborted) return;
      pending.current = null;
      setUncertain(false);
      setPrompt("");
      refresh();
    } catch (cause) {
      if (requestController.signal.aborted) return;
      const rejected =
        cause instanceof ApiError &&
        cause.status >= 400 &&
        cause.status < 500 &&
        cause.status !== 408 &&
        cause.status !== 429;
      setUncertain(!rejected);
      if (rejected) pending.current = null;
      const message = cause instanceof ApiError ? cause.message : "Could not confirm submission.";
      setError(rejected ? message : `${message} Retry to check the same message.`);
      if (cause instanceof ApiError && cause.status === 409) refresh();
    } finally {
      controller.current = null;
      if (!requestController.signal.aborted) setBusy(false);
    }
  }

  return (
    <MessageComposer
      label="Reply"
      prompt={prompt}
      onPromptChange={setPrompt}
      onSubmit={submit}
      busy={busy}
      error={error}
      disabled={disabled}
      readOnly={uncertain}
      placeholder="Continue this conversation…"
      hint={
        uncertain
          ? "Confirm this message before sending another."
          : blocked
            ? "Refreshing chat…"
            : active
              ? "You can reply when the agent finishes."
              : undefined
      }
    />
  );
}
