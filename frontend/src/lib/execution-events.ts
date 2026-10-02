import type { RunDetail } from "./api";
import type { components } from "./api.generated";

export type ToolCheckpoint = components["schemas"]["ToolCheckpoint"];

type Operation = RunDetail["operations"][number];

export type ExecutionEvent = {
  id: string;
  operationId: string;
  attemptId?: string;
  at: string;
  label: string;
  detail?: string;
  failed?: boolean;
  workspaceVersionId?: string;
  workspaceLabel?: string;
  runId?: string;
  runName?: string;
};

export function operationEvents(
  operation: Operation,
  checkpoint?: ToolCheckpoint | null,
  subagents: NonNullable<RunDetail["children"]> = [],
): ExecutionEvent[] {
  const events: ExecutionEvent[] = [
    {
      id: `${operation.id}:planned`,
      operationId: operation.id,
      at: operation.created_at,
      label: "Operation planned",
    },
  ];
  // Attempts are sequential; preserve their causal order even when timestamps tie.
  const attempts = operation.attempts.toSorted((a, b) => a.attempt_no - b.attempt_no);
  for (const attempt of attempts) {
    const identity = { operationId: operation.id, attemptId: attempt.id };
    events.push({
      ...identity,
      id: `${attempt.id}:started`,
      at: attempt.started_at,
      label: `Attempt ${attempt.attempt_no} started`,
    });
    if (attempt.finished_at && (attempt.status === "error" || attempt.status === "unknown")) {
      events.push({
        ...identity,
        id: `${attempt.id}:finished`,
        at: attempt.finished_at,
        label: `Attempt ${attempt.attempt_no} ${attempt.status === "unknown" ? "outcome unknown" : "failed"}`,
        detail: attempt.error?.message ?? "No conclusive result was recorded.",
        failed: attempt.status === "error",
      });
    }
  }
  if (operation.tool_name === "delegate") {
    for (const child of subagents.filter((child) => child.parent_operation_id === operation.id)) {
      const identity = { operationId: operation.id, runId: child.id, runName: child.name };
      events.push({
        ...identity,
        id: `${child.id}:created`,
        at: child.created_at,
        label: "Subagent created",
      });
      if (child.finished_at && (child.status === "succeeded" || child.status === "failed")) {
        events.push({
          ...identity,
          id: `${child.id}:finished`,
          at: child.finished_at,
          label: child.status === "succeeded" ? "Subagent completed" : "Subagent failed",
          failed: child.status === "failed",
          detail: child.terminal_error?.message,
        });
      }
    }
    events.sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  }
  if (operation.completed_at) {
    const accepted = attempts.find((attempt) => attempt.status === "accepted");
    events.push({
      id: `${operation.id}:accepted`,
      operationId: operation.id,
      attemptId: accepted?.id,
      at: operation.completed_at,
      label:
        operation.tool_name === "delegate"
          ? "Subagent reports accepted by Control"
          : "Result accepted by Control",
      detail: accepted ? `From attempt ${accepted.attempt_no}.` : undefined,
      workspaceVersionId:
        operation.result?.kind === "tool" ? operation.result.workspace_version_id : undefined,
      workspaceLabel: operation.tool_name === "delegate" ? "Parent workspace unchanged" : undefined,
    });
  }
  if (checkpoint && operation.kind === "tool" && operation.tool_name !== "delegate") {
    const saved: ExecutionEvent = {
      id: `${operation.id}:saved`,
      operationId: operation.id,
      at: checkpoint.completed_at,
      label: "Result and workspace saved by Tools",
      detail: "The result and complete workspace were saved together.",
      workspaceVersionId: checkpoint.workspace_version_id,
      workspaceLabel: "Saved workspace",
    };
    const next = events.findIndex(
      (event) =>
        event.id === `${operation.id}:accepted` || Date.parse(event.at) > Date.parse(saved.at),
    );
    events.splice(next < 0 ? events.length : next, 0, saved);
  }
  return events;
}
