"use client";

import { useEffect, useState } from "react";
import type { components } from "../lib/api.generated";
import { requestJson, type RunDetail } from "../lib/api";
import { MarkdownAnswer } from "./markdown-answer";
import { OperationOutput } from "./operation-output";
import { ToolArguments } from "./inspection-json";
import { Button } from "./ui/button";
import { SdkInheritedHistory } from "./sdk-inherited-history";

type PreviousTurn = components["schemas"]["PreviousTurn"];
type Inspection = components["schemas"]["OperationInspection"];

function InheritedTurn({ turn }: { turn: PreviousTurn }) {
  const calls = new Map(
    turn.history.flatMap((result) =>
      result.kind === "model"
        ? result.output.tool_calls.map((call) => [call.call_id, call] as const)
        : [],
    ),
  );
  return (
    <article aria-label="Inherited chat turn" className="space-y-8">
      <div
        aria-label="Earlier message"
        className="ml-auto w-fit max-w-[90%] rounded-xl bg-muted px-4 py-3 whitespace-pre-wrap wrap-anywhere"
      >
        {turn.prompt}
      </div>
      <section
        aria-label="Earlier agent response"
        className="m-0 space-y-4 border-0 bg-transparent p-0"
      >
        <h2 className="text-sm font-medium">
          Applied Agent <span className="font-normal text-muted-foreground">· Inherited</span>
        </h2>
        {turn.history.map((result, index) => {
          if (result.kind === "model")
            return result.output.text ? (
              <MarkdownAnswer key={index} text={result.output.text} />
            ) : null;
          const call = calls.get(result.call_id);
          return (
            <details key={index} className="min-w-0 text-xs">
              <summary className="text-muted-foreground">
                Saved tool call · {call?.name ?? "Tool"}
              </summary>
              {call && <ToolArguments tool={call.name} value={call.arguments} />}
              <OperationOutput result={result} />
            </details>
          );
        })}
        {turn.terminal_error && (
          <p className="text-sm text-muted-foreground">
            Earlier turn ended: {turn.terminal_error.message}
          </p>
        )}
      </section>
    </article>
  );
}

export function InheritedHistory({
  run,
  origin,
}: {
  run: RunDetail;
  origin: components["schemas"]["ForkOrigin"];
}) {
  return run.agent ? <SdkInheritedHistory origin={origin} /> : <LegacyInheritedHistory run={run} />;
}

function LegacyInheritedHistory({ run }: { run: RunDetail }) {
  const runId = run.id;
  const operationId = run.operations[0]?.id;
  const [turns, setTurns] = useState<PreviousTurn[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        if (!operationId) throw new Error("The saved branch history is unavailable.");
        const { operation } = await requestJson<Inspection>(
          `/api/runs/${encodeURIComponent(runId)}/operations/${encodeURIComponent(operationId)}`,
          { signal: controller.signal },
        );
        if (operation.kind !== "model" || !operation.request.previous_turns?.length)
          throw new Error("The saved branch history is unavailable.");
        if (!controller.signal.aborted) setTurns(operation.request.previous_turns);
      } catch (cause) {
        if (!controller.signal.aborted)
          setError(cause instanceof Error ? cause.message : "Could not load earlier messages.");
      }
    }
    void load();
    return () => controller.abort();
  }, [runId, operationId, revision]);

  return (
    <section aria-label="Inherited history" className="m-0 space-y-12 border-0 bg-transparent p-0">
      {error ? (
        <div>
          <p role="alert" aria-label="Inherited history error">
            Could not load earlier messages. {error}
          </p>
          <Button
            type="button"
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
        turns.map((turn, index) => <InheritedTurn key={index} turn={turn} />)
      ) : (
        <p role="status" className="text-sm text-muted-foreground">
          Loading earlier messages…
        </p>
      )}
    </section>
  );
}
