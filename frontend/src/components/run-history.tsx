"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { RunDetail } from "../lib/api";
import { cn } from "../lib/utils";
import { OperationInput } from "./operation-input";
import { AttemptMetadata, OperationOutput } from "./operation-output";
import { operationName, OperationSummary } from "./activity-state";
import { InspectionDisclosure } from "./inspection-json";

type Operation = RunDetail["operations"][number];

function OperationRow({ run, operation }: { run: RunDetail; operation: Operation }) {
  const [open, setOpen] = useState(false);
  const current = !operation.completed_at && run.status !== "failed";
  return (
    <li aria-current={current ? "step" : undefined}>
      <details
        aria-label={`${operation.position + 1}. ${operationName(operation)}`}
        className="group/step"
        onToggle={(event) => {
          if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
        }}
      >
        <summary
          className={cn(
            "flex list-none gap-2 border-l-2 border-transparent px-2 py-3 hover:bg-muted/50 [&::-webkit-details-marker]:hidden",
            current && "border-l-foreground bg-muted/50",
          )}
        >
          <ChevronRight
            aria-hidden="true"
            className="mt-1 size-3.5 shrink-0 text-muted-foreground group-open/step:rotate-90"
          />
          <OperationSummary
            key={operation.attempts.at(-1)?.id}
            operation={operation}
            status={run.status}
          />
        </summary>
        {open && (
          <div className="px-3 pb-4 sm:pl-8">
            <OperationOutput operation={operation} />
            <OperationInput runId={run.id} operationId={operation.id} kind={operation.kind} />
            <InspectionDisclosure title="Technical details">
              <p className="run-id">Operation ID: {operation.id}</p>
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
            </InspectionDisclosure>
          </div>
        )}
      </details>
    </li>
  );
}

export function RunHistory({ run }: { run: RunDetail }) {
  return (
    <section aria-label="Execution history" className="mt-3 border-0 p-0">
      <h2 className="sr-only">Execution history</h2>
      {run.operations.length === 0 ? (
        <p className="text-muted-foreground">No operations recorded yet.</p>
      ) : (
        <ol className="m-0 list-none divide-y p-0">
          {run.operations.map((operation) => (
            <OperationRow key={operation.id} run={run} operation={operation} />
          ))}
        </ol>
      )}
      <InspectionDisclosure title="Technical details · run">
        <p className="run-id">Run ID: {run.id}</p>
        <p className="text-xs text-muted-foreground">
          {run.model} ·{" "}
          <time dateTime={run.created_at}>{new Date(run.created_at).toLocaleString()}</time>
        </p>
        {run.next_attempt_at && (
          <p className="text-xs">
            Retry eligible at {new Date(run.next_attempt_at).toLocaleString()}; starts when a worker
            claims it.
          </p>
        )}
      </InspectionDisclosure>
    </section>
  );
}
