"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { RunDetail } from "../lib/api";
import { cn } from "../lib/utils";
import { OperationInspection } from "./operation-inspection";
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
        {open && <OperationInspection run={run} operation={operation} />}
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
