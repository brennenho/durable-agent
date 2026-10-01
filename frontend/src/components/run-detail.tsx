"use client";

import { useEffect, useState } from "react";
import { ApiError, requestJson, type RunDetail } from "../lib/api";

export function RunDetailView({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
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
  }, [runId]);

  return (
    <div>
      {error && (
        <p role="alert" aria-label="Run update error">
          {error}
        </p>
      )}
      {!run && !error && <p role="status">Loading run…</p>}
      {run && (
        <>
          <section aria-labelledby="run-heading">
            <h2 id="run-heading">Run details</h2>
            <p role="status">Status: {run.status}</p>
            <p className="run-id">Run ID: {run.id}</p>
            <p>
              {run.model} · Started {new Date(run.created_at).toLocaleString()}
            </p>
            <p className="preserve-whitespace">{run.prompt}</p>
            {run.status === "retrying" && <p>The last attempt will be retried.</p>}
            {run.terminal_error && (
              <p role="alert" aria-label="Run failure">
                {run.terminal_error.message}
              </p>
            )}
          </section>
          {run.final_answer !== null && (
            <section aria-labelledby="output-heading">
              <h2 id="output-heading">Final output</h2>
              <pre>{run.final_answer || "The agent returned an empty answer."}</pre>
            </section>
          )}
        </>
      )}
    </div>
  );
}
