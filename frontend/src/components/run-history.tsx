import type { RunDetail } from "../lib/api";

export function RunHistory({ operations, status }: Pick<RunDetail, "operations" | "status">) {
  return (
    <section aria-labelledby="history-heading">
      <h2 id="history-heading">Execution history</h2>
      {operations.length === 0 ? (
        <p>No operations recorded yet.</p>
      ) : (
        <ol className="operation-list">
          {operations.map((operation) => (
            <li key={operation.id}>
              <h3>
                {operation.position + 1}. {operation.kind === "model" ? "Model call" : "Tool call"}
              </h3>
              <p>
                {operation.completed_at ? "Completed" : status === "failed" ? "Stopped" : "Pending"}
              </p>
              {operation.attempts.length === 0 && status !== "failed" && (
                <p>Waiting for a worker.</p>
              )}
              <ol className="attempt-list">
                {operation.attempts.map((attempt) => (
                  <li key={attempt.id}>
                    <p>
                      <strong>Attempt {attempt.attempt_no}</strong> · {attempt.status}
                    </p>
                    <p>
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
                      <p className="attempt-error">
                        {attempt.error.code}: {attempt.error.message}
                      </p>
                    )}
                    {attempt.status === "unknown" && <p>No conclusive result was recorded.</p>}
                  </li>
                ))}
              </ol>
              {operation.result?.kind === "tool" && (
                <details>
                  <summary>Saved tool output</summary>
                  <pre>{JSON.stringify(operation.result.output, null, 2)}</pre>
                </details>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
