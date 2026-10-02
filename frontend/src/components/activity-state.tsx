"use client";

import { useEffect, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
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

export function OperationDuration({
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
  const text =
    duration === null ? "—" : `${running ? Math.floor(duration) : Number(duration.toFixed(1))}s`;
  return (
    <span
      title="Latest attempt duration"
      aria-label={`Latest attempt duration: ${duration === null ? "unavailable" : text}`}
    >
      {text}
    </span>
  );
}

export function OperationSummary({
  operation,
  status,
  pauseRequested,
  children,
}: {
  operation: Operation;
  status: RunDetail["status"];
  pauseRequested?: boolean;
  children?: ReactNode;
}) {
  const attempt = operation.attempts.at(-1);
  const running = !operation.completed_at && status !== "failed" && attempt?.status === "running";
  const paused =
    !operation.completed_at && pauseRequested && (status === "queued" || status === "retrying");
  const label = operation.completed_at
    ? "Completed"
    : status === "failed"
      ? "Stopped"
      : running
        ? "Running"
        : paused
          ? "Paused"
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
    <span className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-1 @min-[12rem]:gap-x-2">
      <ChevronRight
        aria-hidden="true"
        className="size-3.5 text-muted-foreground group-open/step:rotate-90"
      />
      <span className="min-w-0 @min-[20rem]:flex @min-[20rem]:items-baseline @min-[20rem]:gap-2">
        <span className="text-xs font-medium wrap-anywhere @min-[12rem]:text-sm @min-[20rem]:shrink-0">
          {operationName(operation)}
        </span>
        {operation.tool_target && (
          <code
            className="block truncate text-xs text-muted-foreground"
            title={operation.tool_target}
          >
            {operation.tool_target}
          </code>
        )}
      </span>
      <span className="flex flex-col items-end gap-1 text-xs leading-5">
        <span>{label}</span>
        {children}
      </span>
      {note && (
        <span className="col-start-2 col-span-2 mt-1 text-xs leading-relaxed text-muted-foreground">
          {note}{" "}
          {paused
            ? "Retry waits for Resume."
            : retried
              ? operation.completed_at
                ? "Retried."
                : "Retrying the same operation."
              : status === "retrying"
                ? "Waiting to retry."
                : ""}
        </span>
      )}
      {output?.status === "error" && (
        <span className="col-start-2 col-span-2 mt-1 text-xs text-destructive">
          {output.message}
        </span>
      )}
      {output?.status === "ok" &&
        typeof output.value.exit_code === "number" &&
        output.value.exit_code !== 0 && (
          <span className="col-start-2 col-span-2 mt-1 text-xs text-destructive">
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
  const now = useNow(active && (!run.pause_requested || run.status === "running"));
  const index = run.operations.findIndex((operation) => !operation.completed_at);
  const operation = run.operations[index];
  const attempt = operation?.attempts.at(-1);
  let label =
    run.status === "succeeded"
      ? "Completed"
      : run.status === "failed"
        ? "Stopped"
        : run.status === "queued"
          ? "Queued"
          : "Running";
  if (active && operation) {
    const phase =
      run.status === "retrying"
        ? run.next_attempt_at && Date.parse(run.next_attempt_at) > now
          ? "Waiting to retry"
          : "Waiting for a worker to retry"
        : attempt?.status === "running"
          ? operation.kind === "model"
            ? "Thinking"
            : operationName(operation)
          : "Queued";
    const elapsed =
      attempt?.status === "running"
        ? ` · ${Math.max(0, Math.floor((now - Date.parse(attempt.started_at)) / 1000))}s`
        : "";
    label = `${phase}${elapsed}`;
  }
  if (active && run.pause_requested) label = run.status === "running" ? "Pausing…" : "Paused";
  return (
    <span
      role="status"
      aria-label="Execution status"
      title={
        active && run.pause_requested && run.status === "running"
          ? "Finishes the current call before pausing"
          : undefined
      }
    >
      {label}
    </span>
  );
}
