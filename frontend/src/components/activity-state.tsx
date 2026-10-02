"use client";

import { useEffect, useState } from "react";
import type { RunDetail } from "../lib/api";

type Operation = RunDetail["operations"][number];

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

export function OperationSummary({
  operation,
  status,
}: {
  operation: Operation;
  status: RunDetail["status"];
}) {
  const attempt = operation.attempts.at(-1);
  const running = !operation.completed_at && status !== "failed" && attempt?.status === "running";
  const now = useNow(running);
  const duration =
    attempt && (attempt.finished_at || running)
      ? Math.max(
          0,
          (attempt.finished_at ? Date.parse(attempt.finished_at) : now) -
            Date.parse(attempt.started_at),
        ) / 1000
      : null;
  const label = operation.completed_at
    ? "Completed"
    : status === "failed"
      ? "Stopped"
      : running
        ? "Running"
        : status === "retrying"
          ? "Retry pending"
          : "Queued";
  const failure = operation.attempts.findLast((item) => item.status === "unknown" || item.error);
  const note =
    failure?.status === "unknown"
      ? "Worker lease expired; outcome unknown."
      : failure?.error?.message;
  const retried = failure && attempt && attempt.attempt_no > failure.attempt_no;
  const output = operation.result?.kind === "tool" ? operation.result.output : null;
  return (
    <span className="@container block min-w-0 flex-1">
      <span className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 @min-[16rem]:grid-cols-[minmax(0,1fr)_auto_3rem]">
        <span className="col-span-2 min-w-0 @min-[16rem]:col-span-1 @min-[16rem]:flex @min-[16rem]:items-baseline @min-[16rem]:gap-2">
          <span className="shrink-0 font-medium">{operationName(operation)}</span>
          {operation.tool_target && (
            <code
              className="block truncate text-xs text-muted-foreground"
              title={operation.tool_target}
            >
              {operation.tool_target}
            </code>
          )}
        </span>
        <span className="text-xs leading-5 @min-[16rem]:text-right">{label}</span>
        <span
          className="text-right text-xs leading-5 text-muted-foreground tabular-nums"
          title="Latest attempt duration"
        >
          {duration === null
            ? "—"
            : `${running ? Math.floor(duration) : Number(duration.toFixed(1))}s`}
        </span>
      </span>
      {note && (
        <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
          {note}{" "}
          {retried
            ? operation.completed_at
              ? "Retried."
              : "Retrying the same operation."
            : status === "retrying"
              ? "Waiting to retry."
              : ""}
        </span>
      )}
      {output?.status === "error" && (
        <span className="mt-1 block text-xs text-destructive">{output.message}</span>
      )}
      {output?.status === "ok" &&
        typeof output.value.exit_code === "number" &&
        output.value.exit_code !== 0 && (
          <span className="mt-1 block text-xs text-destructive">
            Command exited with code {output.value.exit_code}.
          </span>
        )}
    </span>
  );
}

export function operationName(operation: Operation) {
  return operation.kind === "model" ? "Model call" : operation.tool_name || "Tool call";
}

export function ActivityStatus({ run }: { run: RunDetail }) {
  const active = run.status !== "succeeded" && run.status !== "failed";
  const now = useNow(active);
  const index = run.operations.findIndex((operation) => !operation.completed_at);
  const operation = run.operations[index];
  const attempt = operation?.attempts.at(-1);
  let label =
    run.status === "succeeded"
      ? "Completed"
      : run.status === "failed"
        ? "Stopped"
        : run.status === "queued"
          ? "Waiting to start"
          : "Working";
  if (active && operation) {
    const phase =
      run.status === "retrying"
        ? run.next_attempt_at && Date.parse(run.next_attempt_at) > now
          ? "Waiting to retry"
          : "Waiting for a worker to retry"
        : attempt?.status === "running"
          ? operation.kind === "model"
            ? "Waiting for model"
            : "Running tool"
          : "Waiting for a worker";
    const elapsed =
      attempt?.status === "running"
        ? ` · ${Math.max(0, Math.floor((now - Date.parse(attempt.started_at)) / 1000))}s elapsed`
        : "";
    label = `${phase}${operation.kind === "tool" ? `: ${operationName(operation)}` : ""}${elapsed}`;
  }
  return (
    <span role="status" aria-label="Execution status">
      {label}
    </span>
  );
}
