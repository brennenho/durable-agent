"use client";

import { useEffect, useState } from "react";
import { requestJson, type RunDetail } from "../lib/api";
import { operationEvents, type ToolCheckpoint } from "../lib/execution-events";
import { ExecutionEvents } from "./execution-events";

export function useToolCheckpoint(runId: string, operationId: string, enabled: boolean) {
  const [saved, setSaved] = useState<ToolCheckpoint | null>(null);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!enabled || saved) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const value = await requestJson<ToolCheckpoint | null>(
          `/api/runs/${encodeURIComponent(runId)}/operations/${encodeURIComponent(operationId)}/checkpoint`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        if (
          value !== null &&
          (!value.workspace_version_id || !Number.isFinite(Date.parse(value.completed_at)))
        )
          throw new Error("Invalid checkpoint response");
        setSaved(value);
        setCheckedAt(new Date().toISOString());
        setError(false);
        if (value) return; // A saved completion is immutable.
      } catch {
        if (controller.signal.aborted) return;
        setError(true);
      }
      timer = setTimeout(refresh, 2_000);
    }
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId, operationId, enabled, saved]);
  return { saved, checkedAt, error };
}

export function OperationTimeline({
  operation,
  checkpoint,
}: {
  operation: RunDetail["operations"][number];
  checkpoint: ReturnType<typeof useToolCheckpoint>;
}) {
  const { saved, checkedAt, error } = checkpoint;
  return (
    <>
      {operation.kind === "tool" && (
        <div className="mb-3 text-xs text-muted-foreground" aria-live="polite">
          {error ? (
            <p role="alert" aria-label="Checkpoint update error">
              Saved checkpoint unavailable. Retrying…
            </p>
          ) : saved ? (
            !operation.completed_at && (
              <p>Saved by Tools. Control acceptance has not been observed.</p>
            )
          ) : (
            <p>
              {checkedAt ? "No saved checkpoint observed yet." : "Checking for a saved checkpoint…"}
            </p>
          )}
          {checkedAt && !saved && (
            <p>
              Last checked:{" "}
              <time dateTime={checkedAt}>{new Date(checkedAt).toLocaleTimeString()}</time>
            </p>
          )}
        </div>
      )}
      <ExecutionEvents events={operationEvents(operation, saved)} />
    </>
  );
}
