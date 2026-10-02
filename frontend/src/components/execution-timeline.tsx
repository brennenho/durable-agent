"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { RunDetail } from "../lib/api";
import { cn } from "../lib/utils";
import { OperationInspection } from "./operation-inspection";
import { operationName, OperationSummary } from "./activity-state";
import { InspectionDisclosure } from "./inspection-json";
import { BranchDialog } from "./branch-dialog";

type Operation = RunDetail["operations"][number];

function OperationRow({ run, operation }: { run: RunDetail; operation: Operation }) {
  const [open, setOpen] = useState(false);
  const current = !operation.completed_at && run.status !== "failed";
  const result = operation.result;
  const failed = result
    ? result.kind === "model"
      ? result.output.status === "incomplete"
      : result.output.status === "error" ||
        (typeof result.output.value.exit_code === "number" && result.output.value.exit_code !== 0)
    : operation.attempts.at(-1)?.status === "error";
  return (
    <li aria-current={current ? "step" : undefined} className="relative py-1">
      <span
        aria-hidden="true"
        className={cn(
          "absolute top-6 -left-[1.3rem] size-2 rounded-full border bg-background",
          current && "border-foreground bg-foreground",
        )}
      />
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
            failed && "bg-destructive/5 hover:bg-destructive/10",
          )}
        >
          <ChevronRight
            aria-hidden="true"
            className="mt-1 size-3.5 shrink-0 text-muted-foreground group-open/step:rotate-90"
          />
          <span className="min-w-0 flex-1">
            <OperationSummary
              key={operation.attempts.at(-1)?.id}
              operation={operation}
              status={run.status}
            />
            <span className="mt-1 grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 text-xs text-muted-foreground">
              <span>
                Planned{" "}
                <time dateTime={operation.created_at}>
                  {new Date(operation.created_at).toLocaleString()}
                </time>
              </span>
              {operation.checkpoint && <BranchDialog run={run} operation={operation} />}
            </span>
          </span>
        </summary>
        {open && <OperationInspection run={run} operation={operation} />}
      </details>
    </li>
  );
}

function RunMarker({ label, at }: { label: string; at: string }) {
  return (
    <li className="relative flex flex-wrap justify-between gap-x-3 gap-y-1 py-3 text-xs">
      <span
        aria-hidden="true"
        className="absolute top-4 -left-[1.3rem] size-2 rounded-full border bg-background"
      />
      <span className="font-medium">{label}</span>
      <time dateTime={at} className="text-muted-foreground tabular-nums">
        {new Date(at).toLocaleString()}
      </time>
    </li>
  );
}

export function ExecutionTimeline({ run }: { run: RunDetail }) {
  return (
    <section aria-label="Execution history" className="mt-3 border-0 p-0">
      <h2 className="sr-only">Execution history</h2>
      <ol aria-label="Run timeline" className="m-0 ml-1 list-none border-l p-0 pl-4">
        <RunMarker label="Run submitted" at={run.created_at} />
        {run.operations.length === 0 ? (
          <li className="py-2 text-muted-foreground">No operations recorded yet.</li>
        ) : (
          run.operations.map((operation) => (
            <OperationRow key={operation.id} run={run} operation={operation} />
          ))
        )}
        {run.finished_at && (run.status === "succeeded" || run.status === "failed") && (
          <RunMarker
            label={run.status === "succeeded" ? "Run completed" : "Run failed"}
            at={run.finished_at}
          />
        )}
      </ol>
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
