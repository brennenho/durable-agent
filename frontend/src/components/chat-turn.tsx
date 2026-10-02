"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { RunDetail } from "../lib/api";
import { ExecutionTimeline } from "./execution-timeline";
import { ActivityStatus } from "./activity-state";
import { MarkdownAnswer } from "./markdown-answer";
import { RunControls } from "./run-controls";

export function ChatTurn({
  run,
  onUpdate,
}: {
  run: RunDetail;
  onUpdate: (run: RunDetail) => void;
}) {
  const [activityOpen, setActivityOpen] = useState(false);
  const finished = run.status === "succeeded" || run.status === "failed";
  const steps = run.operations.length;

  return (
    <article aria-label="Chat turn" className="space-y-8">
      <div
        aria-label="Your message"
        className="ml-auto w-fit max-w-[90%] rounded-xl bg-muted px-4 py-3 whitespace-pre-wrap wrap-anywhere"
      >
        {run.prompt}
      </div>
      <section aria-label="Agent response" className="m-0 border-0 bg-transparent p-0">
        <div className="mb-2 flex items-start justify-between gap-3">
          <h2 className="m-0 shrink-0 py-1.5 text-sm font-medium">Applied Agent</h2>
          {!finished && <RunControls run={run} onUpdate={onUpdate} />}
        </div>
        <details
          aria-label="Activity"
          className="group/activity mb-5"
          onToggle={(event) => {
            if (event.target === event.currentTarget) setActivityOpen(event.currentTarget.open);
          }}
        >
          <summary className="flex w-fit list-none items-center gap-2 text-xs text-muted-foreground [&::-webkit-details-marker]:hidden">
            <ChevronRight
              aria-hidden="true"
              className="size-3.5 transition-transform group-open/activity:rotate-90"
            />
            <ActivityStatus run={run} />
            {steps > 0 && (
              <span>
                · {steps} {steps === 1 ? "step" : "steps"}
              </span>
            )}
          </summary>
          {activityOpen && <ExecutionTimeline run={run} />}
        </details>
        {run.terminal_error && (
          <p
            role="alert"
            aria-label="Run failure"
            className="rounded-md border border-destructive/20 bg-destructive/5 p-4"
          >
            {run.terminal_error.message}
          </p>
        )}
        {(run.final_answer !== null || (finished && !run.terminal_error)) && (
          <section aria-label="Final output" className="m-0 border-0 bg-transparent p-0">
            {run.final_answer ? (
              <MarkdownAnswer text={run.final_answer} />
            ) : (
              <p className="text-muted-foreground">No final answer.</p>
            )}
          </section>
        )}
      </section>
    </article>
  );
}
