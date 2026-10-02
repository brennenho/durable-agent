"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import type { RunDetail } from "../lib/api";
import { ExecutionTimeline } from "./execution-timeline";
import { ActivityStatus } from "./activity-state";
import { RunOutput } from "./run-output";
import { RunControls } from "./run-controls";
import { FailedAttemptsBadge } from "./failed-attempts-badge";
import { SubagentList } from "./subagent-list";

export function ChatTurn({
  run,
  onUpdate,
  live,
}: {
  run: RunDetail;
  onUpdate: (run: RunDetail) => void;
  live: boolean;
}) {
  const [activityOpen, setActivityOpen] = useState(false);
  const finished = run.status === "succeeded" || run.status === "failed";
  const steps = run.operations.length;

  return (
    <article aria-label="Chat turn" className="space-y-8">
      {run.parent && (
        <div aria-label="Subagent context" className="space-y-2 text-xs text-muted-foreground">
          <Link
            href={`/chats/${encodeURIComponent(run.parent.conversation_id)}`}
            className="underline underline-offset-4"
          >
            Back to parent chat
          </Link>
          <p className="m-0">{run.parent.name} · Managed by the parent agent</p>
        </div>
      )}
      <div
        aria-label="Your message"
        className="ml-auto w-fit max-w-[90%] rounded-xl bg-muted px-4 py-3 whitespace-pre-wrap wrap-anywhere"
      >
        {run.prompt}
      </div>
      <section aria-label="Agent response" className="m-0 border-0 bg-transparent p-0">
        <div className="mb-2 flex items-start justify-between gap-3">
          <h2 className="m-0 min-w-0 flex-1 py-1.5 text-sm font-medium wrap-anywhere">
            {!run.agent || run.agent.name === "builtin" ? "Applied Agent" : run.agent.name}
          </h2>
          {!finished && !run.parent && <RunControls run={run} onUpdate={onUpdate} />}
        </div>
        <details
          aria-label="Activity"
          className="group/activity mb-5"
          onToggle={(event) => {
            if (event.target === event.currentTarget) setActivityOpen(event.currentTarget.open);
          }}
        >
          <summary className="flex w-fit list-none flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground [&::-webkit-details-marker]:hidden">
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
            <FailedAttemptsBadge operations={run.operations} />
          </summary>
          {activityOpen && <ExecutionTimeline run={run} live={live && !finished} />}
        </details>
        {run.status === "waiting" && run.children?.length ? (
          <section aria-label="Subagent progress" className="m-0 space-y-3 border-0 p-0">
            <h3 className="text-xs font-medium">Subagents</h3>
            <SubagentList runs={run.children} />
            <p className="text-xs text-muted-foreground">
              Reports return here. Each subagent keeps its own files.
            </p>
          </section>
        ) : null}
        {run.terminal_error && (
          <p
            role="alert"
            aria-label="Run failure"
            className="rounded-md border border-destructive/20 bg-destructive/5 p-4"
          >
            {run.terminal_error.message}
          </p>
        )}
        <RunOutput run={run} />
      </section>
    </article>
  );
}
