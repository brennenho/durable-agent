import type { RunDetail } from "../lib/api";
import { Badge } from "./ui/badge";
import { OperationInput } from "./operation-input";
import { AttemptMetadata, OperationOutput } from "./operation-output";
import { loopPosition, operationName } from "./activity-state";

export function RunHistory({ run }: { run: RunDetail }) {
  const { operations, status } = run;
  return (
    <section aria-label="Execution history" className="mt-4 border-0 p-0">
      <h2 className="sr-only">Execution history</h2>
      {operations.length === 0 ? (
        <p className="text-muted-foreground">No operations recorded yet.</p>
      ) : (
        <ol className="operation-list">
          {operations.map((operation, index) => (
            <li
              key={operation.id}
              aria-current={!operation.completed_at && status !== "failed" ? "step" : undefined}
              className={
                !operation.completed_at && status !== "failed"
                  ? "border-l-2 border-l-foreground pl-3"
                  : undefined
              }
            >
              <div className="mt-5 flex items-center justify-between gap-3">
                <h3 className="flex items-center gap-2">
                  {operation.position + 1}. {operationName(operation)}
                </h3>
                <Badge variant="secondary" className="font-normal">
                  {operation.completed_at
                    ? "Completed"
                    : status === "failed"
                      ? "Stopped"
                      : operation.attempts.at(-1)?.status === "running"
                        ? "Running"
                        : status === "retrying"
                          ? "Retry pending"
                          : "Queued"}
                </Badge>
              </div>
              <p className="my-2 text-xs text-muted-foreground">
                {loopPosition(operations, index)}
              </p>
              {operation.tool_target && (
                <pre className="my-2 rounded bg-muted p-2">{operation.tool_target}</pre>
              )}
              {!operation.completed_at && status === "retrying" && run.next_attempt_at && (
                <p className="text-xs">
                  Retry eligible at {new Date(run.next_attempt_at).toLocaleTimeString()}; starts
                  when a worker claims it.
                </p>
              )}
              {operation.attempts.some(
                (attempt) => attempt.error?.retryable || attempt.status === "unknown",
              ) && (
                <p className="text-xs text-muted-foreground">
                  {operation.kind === "tool"
                    ? "Retry policy: reuse a saved tool result, or start from the same input files."
                    : "Retry policy: another model request may be needed if no response was saved."}
                </p>
              )}
              {!operation.completed_at &&
                operation.attempts.length === 0 &&
                status !== "failed" && (
                  <p className="text-muted-foreground">Waiting for a worker.</p>
                )}
              <ol className="attempt-list">
                {operation.attempts.map((attempt) => (
                  <li key={attempt.id}>
                    <p className="my-1">
                      <strong>
                        Attempt {attempt.attempt_no}
                        {run.max_attempts ? ` of ${run.max_attempts}` : ""}
                      </strong>{" "}
                      · {attempt.status}
                    </p>
                    <p className="my-1 text-xs text-muted-foreground">
                      Started{" "}
                      <time dateTime={attempt.started_at}>
                        {new Date(attempt.started_at).toLocaleString()}
                      </time>
                      {attempt.finished_at && (
                        <>
                          {" "}
                          · Finished{" "}
                          <time dateTime={attempt.finished_at}>
                            {new Date(attempt.finished_at).toLocaleString()}
                          </time>
                        </>
                      )}
                    </p>
                    <AttemptMetadata attempt={attempt} kind={operation.kind} />
                    {attempt.error && (
                      <p className="attempt-error my-2 rounded-md bg-destructive/5 px-3 py-2 text-xs">
                        {attempt.error.code}: {attempt.error.message}
                      </p>
                    )}
                    {attempt.error && (
                      <p className="my-2 text-xs text-muted-foreground">
                        {attempt.error.retryable
                          ? "This error allows another attempt within the attempt limit."
                          : "This error is not retryable."}
                      </p>
                    )}
                    {attempt.status === "unknown" && (
                      <p className="my-2 text-xs text-muted-foreground">
                        No conclusive result was recorded. The worker did not report before its
                        lease expired.
                      </p>
                    )}
                  </li>
                ))}
              </ol>
              {operation.completed_at && (
                <p className="text-xs text-muted-foreground">
                  {operations[index + 1]?.kind === "tool"
                    ? "Next: execute the next requested tool."
                    : operations[index + 1]?.kind === "model"
                      ? "Next: request a model response with the saved tool results."
                      : status === "succeeded"
                        ? "Final answer returned; run completed."
                        : status === "failed"
                          ? `Run stopped: ${run.terminal_error?.message || "see run failure."}`
                          : null}
                </p>
              )}
              <OperationInput runId={run.id} operationId={operation.id} kind={operation.kind} />
              <OperationOutput operation={operation} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
