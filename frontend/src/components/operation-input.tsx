"use client";

import { useEffect, useRef, useState } from "react";
import type { components } from "../lib/api.generated";
import { OperationOutput } from "./operation-output";
import { Button } from "./ui/button";
import { requestJson } from "../lib/api";
import { InspectionDisclosure, InspectionJson, ToolArguments } from "./inspection-json";

type Inspection = components["schemas"]["OperationInspection"];

function checkedInput(data: Inspection, kind: "model" | "tool") {
  const { operation, config } = data ?? {};
  const request = operation?.request;
  if (
    !request ||
    operation.kind !== kind ||
    typeof config?.model !== "string" ||
    !Array.isArray(config.tool_schemas)
  )
    throw new Error("The saved input response is invalid.");
  if (
    operation.kind === "model" &&
    (typeof operation.request.prompt !== "string" ||
      !Array.isArray(operation.request.history) ||
      (operation.request.previous_turns !== undefined &&
        (!Array.isArray(operation.request.previous_turns) ||
          operation.request.previous_turns.some(
            (turn) => !turn || typeof turn.prompt !== "string" || !Array.isArray(turn.history),
          ))))
  )
    throw new Error("The saved model input is invalid.");
  if (
    operation.kind === "tool" &&
    (!operation.request.call ||
      typeof operation.request.call.name !== "string" ||
      typeof operation.request.call.arguments !== "object" ||
      operation.request.call.arguments === null)
  )
    throw new Error("The saved tool input is invalid.");
  return data;
}

function ResultHistory({ history }: { history: components["schemas"]["ModelRequest"]["history"] }) {
  const toolNames = new Map(
    history.flatMap((result) =>
      result.kind === "model"
        ? result.output.tool_calls.map((call) => [call.call_id, call.name] as const)
        : [],
    ),
  );
  return history.length === 0 ? (
    <p className="text-xs text-muted-foreground">No earlier results.</p>
  ) : (
    <ol className="list-none space-y-5 p-0">
      {history.map((result, index) => (
        <li key={index}>
          <h4 className="my-2 text-xs font-medium">
            {result.kind === "model"
              ? "Model response"
              : toolNames.get(result.call_id) || "Tool result"}
          </h4>
          <OperationOutput result={result} />
        </li>
      ))}
    </ol>
  );
}

function InputContent({ data }: { data: Inspection }) {
  const { operation } = data;
  if (operation.kind === "tool") return <ToolArguments value={operation.request.call.arguments} />;
  const previous = operation.request.previous_turns ?? [];
  return (
    <>
      {previous.map((turn, index) => (
        <div key={index} className="my-5">
          <h4 className="text-xs font-medium">Previous turn {index + 1}</h4>
          <p className="whitespace-pre-wrap">{turn.prompt}</p>
          {turn.terminal_error && (
            <p className="text-xs text-destructive">
              This turn failed: {turn.terminal_error.message}
            </p>
          )}
          <ResultHistory history={turn.history} />
        </div>
      ))}
      <h4 className="mt-5 text-xs font-medium">Earlier results in this turn</h4>
      <ResultHistory history={operation.request.history} />
    </>
  );
}

function InputTechnical({ data }: { data: Inspection }) {
  const { operation, config } = data;
  return (
    <>
      {operation.kind === "tool" ? (
        <p className="run-id">
          Call ID: {operation.request.call.call_id}
          <br />
          Input workspace: {operation.request.input_version_id}
        </p>
      ) : (
        <InspectionDisclosure title="Raw saved context">
          <p className="text-xs text-muted-foreground">
            Provider input is assembled from these saved results. Interrupted turns receive replay
            adjustments.
          </p>
          <InspectionJson
            value={{
              previous_turns: operation.request.previous_turns ?? [],
              history: operation.request.history,
            }}
            hideEncrypted
          />
        </InspectionDisclosure>
      )}
      <InspectionDisclosure title="Configuration, tool schemas, and limits">
        <InspectionJson value={config} />
      </InspectionDisclosure>
    </>
  );
}

export function useOperationInput(runId: string, operationId: string, kind: "model" | "tool") {
  const [data, setData] = useState<Inspection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  function cancel() {
    controller.current?.abort();
    controller.current = null;
  }

  function load() {
    if (data || controller.current) return;
    const pending = new AbortController();
    controller.current = pending;
    setError(null);
    requestJson<Inspection>(
      `/api/runs/${encodeURIComponent(runId)}/operations/${encodeURIComponent(operationId)}`,
      { signal: pending.signal },
    )
      .then((value) => checkedInput(value, kind))
      .then((value) => {
        if (!pending.signal.aborted) setData(value);
      })
      .catch((cause) => {
        if (!pending.signal.aborted)
          setError(cause instanceof Error ? cause.message : "Could not load the saved input.");
      })
      .finally(() => {
        if (controller.current === pending) controller.current = null;
      });
  }
  return { data, error, load, cancel };
}

export function OperationInput({
  input,
  technical = false,
}: {
  input: ReturnType<typeof useOperationInput>;
  technical?: boolean;
}) {
  if (input.error)
    return (
      <div>
        <p role="alert" aria-label="Input inspection error" className="text-xs">
          {input.error}
        </p>
        <Button type="button" variant="outline" size="sm" onClick={input.load}>
          Retry inspection
        </Button>
      </div>
    );
  if (!input.data)
    return (
      <p className="text-xs text-muted-foreground" aria-live="polite">
        Loading saved input…
      </p>
    );
  return technical ? <InputTechnical data={input.data} /> : <InputContent data={input.data} />;
}
