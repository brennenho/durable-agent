import { ChevronRight } from "lucide-react";
import type { RunDetail } from "../lib/api";
import { RunHistory } from "./run-history";
import { SavedFiles } from "./saved-files";
import { MarkdownAnswer } from "./markdown-answer";

const statusLabels = {
  queued: "Waiting to start",
  running: "Working",
  retrying: "Retrying",
  succeeded: "Completed",
  failed: "Stopped",
};

export function ChatTurn({ run }: { run: RunDetail }) {
  const finished = run.status === "succeeded" || run.status === "failed";
  const completed = run.operations.filter((operation) => operation.completed_at !== null).length;

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
        <details aria-label="Activity" className="group mb-5">
          <summary className="flex w-fit list-none items-center gap-2 text-xs text-muted-foreground [&::-webkit-details-marker]:hidden">
            <ChevronRight
              aria-hidden="true"
              className="size-3.5 transition-transform group-open:rotate-90"
            />
            <span role="status" aria-label="Execution status">
              {statusLabels[run.status]}
            </span>
            {completed > 0 && (
              <span>
                · {completed} {completed === 1 ? "step" : "steps"}
              </span>
            )}
          </summary>
          <div className="mt-4 rounded-lg border p-4">
            <p className="run-id m-0">Run ID: {run.id}</p>
            <p className="text-xs text-muted-foreground">
              {run.model} ·{" "}
              <time dateTime={run.created_at}>{new Date(run.created_at).toLocaleString()}</time>
            </p>
            <RunHistory operations={run.operations} status={run.status} />
          </div>
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
        {run.status === "retrying" && (
          <p className="text-sm text-muted-foreground">The last attempt failed. Retrying…</p>
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
        <SavedFiles runId={run.id} workspaceVersionId={run.workspace_version_id} />
      </section>
    </article>
  );
}
