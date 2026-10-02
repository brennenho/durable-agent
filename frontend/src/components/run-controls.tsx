"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, requestJson, type RunDetail, type RunSummary } from "../lib/api";
import { Button } from "./ui/button";

type Action = "pause" | "resume";

export function RunControls({
  run,
  onUpdate,
}: {
  run: RunDetail;
  onUpdate: (run: RunDetail) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryAction, setRetryAction] = useState<Action | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const action = retryAction ?? (run.pause_requested ? "resume" : "pause");

  async function submit() {
    if (controller.current) return;
    const current = new AbortController();
    controller.current = current;
    setBusy(true);
    setError(null);
    try {
      const path = `/api/runs/${encodeURIComponent(run.id)}`;
      await requestJson<RunSummary>(`${path}/${action}`, {
        method: "POST",
        signal: current.signal,
      });
      const saved = await requestJson<RunDetail>(path, { signal: current.signal });
      if (current.signal.aborted) return;
      onUpdate(saved);
      setRetryAction(null);
    } catch (cause) {
      if (current.signal.aborted) return;
      // Keep an uncertain action fixed even if polling observes its effect.
      setRetryAction(action);
      setError(
        `${cause instanceof ApiError ? cause.message : "Could not confirm the change."} Retry ${action} to confirm.`,
      );
    } finally {
      controller.current = null;
      if (!current.signal.aborted) setBusy(false);
    }
  }

  return (
    <div className="mt-4 space-y-2" aria-label="Run controls">
      <Button size="sm" variant="outline" disabled={busy} onClick={submit}>
        {busy
          ? "Saving…"
          : retryAction
            ? `Retry ${action}`
            : action === "pause"
              ? "Pause"
              : "Resume"}
      </Button>
      {error && (
        <p role="alert" aria-label="Run control error" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
