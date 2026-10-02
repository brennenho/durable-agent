"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, requestJson, type RunDetail } from "../lib/api";
import { ChatTurn } from "./chat-turn";
import { ChatWorkspace } from "./chat-workspace";

export function RunDetailView({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const polling = useRef<AbortController | null>(null);

  function updateRun(saved: RunDetail) {
    polling.current?.abort();
    setRun(saved);
    setError(null);
    setRevision((value) => value + 1);
  }

  useEffect(() => {
    const controller = new AbortController();
    polling.current = controller;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      let retry = true;
      try {
        const latest = await requestJson<RunDetail>(`/api/runs/${encodeURIComponent(runId)}`, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setRun(latest);
        setError(null);
        retry = latest.status !== "succeeded" && latest.status !== "failed";
      } catch (cause) {
        if (controller.signal.aborted) return;
        const permanent =
          cause instanceof ApiError &&
          cause.status >= 400 &&
          cause.status < 500 &&
          cause.status !== 408 &&
          cause.status !== 429;
        const message = cause instanceof ApiError ? cause.message : "Could not refresh this run.";
        setError(permanent ? message : `${message} Retrying…`);
        retry = !permanent;
      }
      if (retry) timer = setTimeout(refresh, 2_000);
    }
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId, revision]);

  return (
    <ChatWorkspace runs={run ? [run] : []}>
      <div className="space-y-6">
        {error && (
          <p
            role="alert"
            aria-label="Run update error"
            className="rounded-md border border-destructive/20 bg-destructive/5 p-4"
          >
            {error}
          </p>
        )}
        {!run && !error && <p role="status">Loading chat…</p>}
        {run && <ChatTurn run={run} onUpdate={updateRun} />}
      </div>
    </ChatWorkspace>
  );
}
