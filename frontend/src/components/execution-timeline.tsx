"use client";

import { useState } from "react";
import type { RunDetail } from "../lib/api";
import { cn } from "../lib/utils";
import { groupIsPausing } from "../lib/subagents";
import { OperationInspection } from "./operation-inspection";
import { operationName, OperationDuration, OperationSummary } from "./activity-state";
import { InspectionDisclosure } from "./inspection-json";
import { BranchDialog } from "./branch-dialog";
import { TimelineStamp } from "./timeline-stamp";
import { SubagentList } from "./subagent-list";

type Operation = RunDetail["operations"][number];

function OperationRow({
  run,
  operation,
  live,
}: {
  run: RunDetail;
  operation: Operation;
  live: boolean;
}) {
  const [open, setOpen] = useState(false);
  const current = !operation.completed_at && run.status !== "failed";
  const result = operation.result;
  const children = (run.children ?? []).filter(
    (child) => child.parent_operation_id === operation.id,
  );
  const failed = result
    ? result.kind === "model"
      ? result.output.status === "incomplete"
      : result.output.status === "error" ||
        (typeof result.output.value.exit_code === "number" && result.output.value.exit_code !== 0)
    : operation.attempts.at(-1)?.status === "error";
  return (
    <li aria-current={current ? "step" : undefined} className="grid grid-cols-subgrid col-span-3">
      <TimelineStamp
        at={operation.created_at}
        label="Operation planned"
        active={current}
        live={live && current}
        duration={
          operation.tool_name !== "delegate" && (
            <OperationDuration
              key={operation.attempts.at(-1)?.id}
              operation={operation}
              status={run.status}
            />
          )
        }
      />
      <details
        aria-label={`${operation.position + 1}. ${operationName(operation)}`}
        className="group/step min-w-0"
        onToggle={(event) => {
          if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
        }}
      >
        <summary
          className={cn(
            "@container list-none border-l-2 border-transparent px-2 py-3 hover:bg-muted/50 [&::-webkit-details-marker]:hidden",
            current && "border-l-primary",
            current && !failed && "bg-primary/5 hover:bg-primary/8",
            failed && "bg-destructive/5 hover:bg-destructive/10",
          )}
        >
          <OperationSummary
            operation={operation}
            status={run.status}
            pauseRequested={run.pause_requested}
            pausing={groupIsPausing(run)}
          >
            {operation.checkpoint && !run.parent && (
              <BranchDialog run={run} operation={operation} />
            )}
          </OperationSummary>
        </summary>
        {open && (
          // Span the clock column, dot, and gaps on narrow screens (3.75 + .5 + 1 rem).
          <div className="relative -ml-21 min-w-0 bg-background sm:ml-0">
            <OperationInspection run={run} operation={operation} />
          </div>
        )}
      </details>
      {children.length > 0 && run.status !== "waiting" && (
        <div className="col-start-3 min-w-0 pb-4">
          <SubagentList runs={children} />
        </div>
      )}
    </li>
  );
}

function RunMarker({ label, at }: { label: string; at: string }) {
  return (
    <li className="col-span-3 grid grid-cols-subgrid text-xs">
      <TimelineStamp at={at} label={label} />
      <span className="py-3.5 font-medium">{label}</span>
    </li>
  );
}

export function ExecutionTimeline({ run, live }: { run: RunDetail; live: boolean }) {
  return (
    <section aria-label="Execution history" className="mt-3 border-0 p-0">
      <h2 className="sr-only">Execution history</h2>
      <ol
        aria-label="Run timeline"
        className="m-0 grid list-none grid-cols-[3.75rem_0.5rem_minmax(0,1fr)] gap-x-2 p-0 sm:gap-x-3"
      >
        <RunMarker label="Run submitted" at={run.created_at} />
        {run.operations.length === 0 ? (
          <li className="col-start-3 py-2 text-muted-foreground">No operations recorded yet.</li>
        ) : (
          run.operations.map((operation) => (
            <OperationRow key={operation.id} run={run} operation={operation} live={live} />
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
        {run.next_attempt_at && !run.pause_requested && (
          <p className="text-xs">
            Retry eligible at {new Date(run.next_attempt_at).toLocaleString()}; starts when a worker
            claims it.
          </p>
        )}
      </InspectionDisclosure>
    </section>
  );
}
