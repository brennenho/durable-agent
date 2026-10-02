"use client";

import { useEffect, useState } from "react";
import type { RunDetail } from "../lib/api";

type Operation = RunDetail["operations"][number];

export function operationName(operation: Operation) {
  return operation.kind === "model" ? "Model call" : operation.tool_name || "Tool call";
}

export function loopPosition(operations: Operation[], index: number) {
  const preceding = operations.slice(0, index + 1);
  const rounds = preceding.filter((operation) => operation.kind === "model");
  const model = rounds.at(-1);
  const round = `Model round ${rounds.length || 1}`;
  if (operations[index].kind === "model") return round;
  const calls = model?.result?.kind === "model" ? model.result.output.tool_calls : [];
  const toolIndex = preceding.filter(
    (operation) => operation.kind === "tool" && operation.position > (model?.position ?? -1),
  ).length;
  return calls.length ? `${round} · Tool ${toolIndex} of ${calls.length}` : "Tool call";
}

export function ActivityStatus({ run }: { run: RunDetail }) {
  const [now, setNow] = useState(() => Date.now());
  const active = run.status !== "succeeded" && run.status !== "failed";
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
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
    label = `${loopPosition(run.operations, index)} · ${phase}${operation.kind === "tool" ? `: ${operationName(operation)}` : ""}${elapsed}`;
  }
  return (
    <span role="status" aria-label="Execution status">
      {label}
    </span>
  );
}
