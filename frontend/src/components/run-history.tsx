import type { RunDetail } from "../lib/api";
import { Badge } from "./ui/badge";

export function RunHistory({ operations, status }: Pick<RunDetail, "operations" | "status">) {
  return (
    <section aria-label="Execution history" className="mt-4 border-0 p-0">
      <h2 className="sr-only">Execution history</h2>
      {operations.length === 0 ? (
        <p className="text-muted-foreground">No operations recorded yet.</p>
      ) : (
        <ol className="operation-list">
          {operations.map((operation) => (
            <li key={operation.id}>
              <div className="mt-5 flex items-center justify-between gap-3">
                <h3 className="flex items-center gap-2">
                  {operation.position + 1}.{" "}
                  {operation.kind === "model" ? "Model call" : "Tool call"}
                </h3>
                <Badge variant="secondary" className="font-normal">
                  {operation.completed_at
                    ? "Completed"
                    : status === "failed"
                      ? "Stopped"
                      : "Pending"}
                </Badge>
              </div>
              {operation.attempts.length === 0 && status !== "failed" && (
                <p className="text-muted-foreground">Waiting for a worker.</p>
              )}
              <ol className="attempt-list">
                {operation.attempts.map((attempt) => (
                  <li key={attempt.id}>
                    <p className="my-1">
                      <strong>Attempt {attempt.attempt_no}</strong> · {attempt.status}
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
                    {attempt.error && (
                      <p className="attempt-error my-2 rounded-md bg-destructive/5 px-3 py-2 text-xs">
                        {attempt.error.code}: {attempt.error.message}
                      </p>
                    )}
                    {attempt.status === "unknown" && (
                      <p className="my-2 text-xs text-muted-foreground">
                        No conclusive result was recorded.
                      </p>
                    )}
                  </li>
                ))}
              </ol>
              {operation.result?.kind === "tool" && (
                <details className="mt-4 rounded-md border bg-background px-4 py-3">
                  <summary className="text-xs font-medium">Saved tool output</summary>
                  <pre className="mt-3">{JSON.stringify(operation.result.output, null, 2)}</pre>
                </details>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
