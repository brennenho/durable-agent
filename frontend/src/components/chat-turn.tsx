"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { RunDetail } from "../lib/api";
import { ExecutionTimeline } from "./execution-timeline";
import { ActivityStatus } from "./activity-state";
import { MarkdownAnswer } from "./markdown-answer";

export function ChatTurn({ run }: { run: RunDetail }) {
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
        <h2 className="mb-3 text-sm font-medium">Applied Agent</h2>
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
        <section aria-label="Final output" className="m-0 border-0 bg-transparent p-0">
          {run.final_answer !== null ? (
            <MarkdownAnswer text={run.final_answer || "The agent returned an empty answer."} />
          ) : (
            <p className="text-muted-foreground">
              {finished
                ? "This task ended without a final answer."
                : run.status === "queued"
                  ? "Your task is queued."
                  : "Working on your task…"}
            </p>
          )}
        </section>
      </section>
    </article>
  );
}
