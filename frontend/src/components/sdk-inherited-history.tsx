"use client";

import { useEffect, useState } from "react";
import { requestJson, type ConversationDetail, type RunDetail } from "../lib/api";
import { OperationOutput } from "./operation-output";
import { RunOutput } from "./run-output";
import { Button } from "./ui/button";

type Origin = NonNullable<ConversationDetail["forked_from"]>;
type InheritedRun = { run: RunDetail; complete: boolean };

async function loadHistory(origin: Origin, signal: AbortSignal): Promise<InheritedRun[]> {
  const turns: InheritedRun[] = [];
  const visited = new Set<string>();
  let source: Origin | null = origin;
  while (source) {
    if (visited.has(source.conversation_id)) throw new Error("Invalid branch lineage.");
    visited.add(source.conversation_id);
    const conversation: ConversationDetail = await requestJson(
      `/api/conversations/${encodeURIComponent(source.conversation_id)}`,
      { signal },
    );
    const index = conversation.runs.findIndex((run) => run.id === source?.run_id);
    const selected = conversation.runs[index];
    const checkpoint = selected?.operations.find((step) => step.id === source?.operation_id);
    if (!checkpoint?.completed_at || !checkpoint.result || checkpoint.position !== source.position)
      throw new Error("The saved branch checkpoint is unavailable.");
    // The source may continue after the fork. Only the accepted prefix belongs to this branch.
    const prefix = selected.operations.filter(
      (step) => step.position <= checkpoint.position && step.completed_at && step.result,
    );
    turns.unshift(...conversation.runs.slice(0, index).map((run) => ({ run, complete: true })), {
      run: { ...selected, operations: prefix },
      complete: false,
    });
    source = conversation.forked_from ?? null;
  }
  return turns;
}

function SavedTurn({ run, complete }: InheritedRun) {
  return (
    <article aria-label="Inherited chat turn" className="space-y-8">
      {run.prompt && (
        <div
          aria-label="Earlier message"
          className="ml-auto w-fit max-w-[90%] rounded-xl bg-muted px-4 py-3 whitespace-pre-wrap wrap-anywhere"
        >
          {run.prompt}
        </div>
      )}
      <section
        aria-label="Earlier agent response"
        className="m-0 space-y-4 border-0 bg-transparent p-0"
      >
        <h2 className="text-sm font-medium wrap-anywhere">
          {!run.agent || run.agent.name === "builtin" ? "Applied Agent" : run.agent.name}
          <span className="font-normal text-muted-foreground"> · Inherited</span>
        </h2>
        {run.operations.map((step) =>
          step.completed_at && step.result ? (
            <details key={step.id} className="min-w-0 text-xs">
              <summary className="text-muted-foreground">
                {step.kind === "model"
                  ? "Saved model call"
                  : `Saved tool call · ${step.tool_name || "Tool"}`}
              </summary>
              <OperationOutput result={step.result} />
            </details>
          ) : null,
        )}
        {complete && <RunOutput run={run} />}
        {complete && run.terminal_error && (
          <p className="text-sm text-muted-foreground">
            Earlier turn ended: {run.terminal_error.message}
          </p>
        )}
      </section>
    </article>
  );
}

export function SdkInheritedHistory({ origin }: { origin: Origin }) {
  const { conversation_id, run_id, operation_id, position } = origin;
  const [turns, setTurns] = useState<InheritedRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void loadHistory({ conversation_id, run_id, operation_id, position }, controller.signal)
      .then((history) => {
        if (!controller.signal.aborted) setTurns(history);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted)
          setError(cause instanceof Error ? cause.message : "Could not load earlier messages.");
      });
    return () => controller.abort();
  }, [conversation_id, run_id, operation_id, position, revision]);

  return (
    <section aria-label="Inherited history" className="m-0 space-y-12 border-0 bg-transparent p-0">
      {error ? (
        <div>
          <p role="alert" aria-label="Inherited history error">
            Could not load earlier messages. {error}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setError(null);
              setRevision((value) => value + 1);
            }}
          >
            Retry history
          </Button>
        </div>
      ) : turns ? (
        turns.map((turn) => <SavedTurn key={turn.run.id} {...turn} />)
      ) : (
        <p role="status" className="text-sm text-muted-foreground">
          Loading earlier messages…
        </p>
      )}
    </section>
  );
}
