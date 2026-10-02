import type { RunDetail } from "./api";

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
};

export function operationEvents(operation: Operation): ExecutionEvent[] {
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
  if (operation.completed_at) {
    const accepted = attempts.find((attempt) => attempt.status === "accepted");
    events.push({
      id: `${operation.id}:accepted`,
      operationId: operation.id,
      attemptId: accepted?.id,
      at: operation.completed_at,
      label: "Result accepted by Control",
      detail: accepted ? `From attempt ${accepted.attempt_no}.` : undefined,
      workspaceVersionId:
        operation.result?.kind === "tool" ? operation.result.workspace_version_id : undefined,
    });
  }
  return events;
}
