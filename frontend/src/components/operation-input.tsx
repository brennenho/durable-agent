"use client";

import { useEffect, useRef, useState } from "react";
import type { components } from "../lib/api.generated";
import { requestJson } from "../lib/api";
import { InspectionDisclosure, InspectionJson } from "./inspection-json";

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

function InputContent({ data }: { data: Inspection }) {
  const { operation, config } = data;
  if (operation.kind === "tool")
    return (
      <>
        <p className="text-xs">
          {operation.request.call.name} · Call ID: {operation.request.call.call_id}
        </p>
        <h4 className="text-xs font-medium">Arguments</h4>
        <InspectionJson value={operation.request.call.arguments} />
        <p className="text-xs text-muted-foreground">
          Input workspace: {operation.request.input_version_id}
        </p>
      </>
    );
  const previous = operation.request.previous_turns ?? [];
  return (
    <>
      <p className="text-xs text-muted-foreground">
        Saved logical input. The worker assembles the provider request from this context.
      </p>
      <p className="text-xs">Requested model: {config.model}</p>
      <h4 className="text-xs font-medium">Prompt</h4>
      <pre className="my-2">{operation.request.prompt}</pre>
      <InspectionDisclosure title={`Previous turns (${previous.length})`}>
        {previous.length === 0 ? (
          <p className="text-xs">No previous turns.</p>
        ) : (
          previous.map((turn, index) => (
            <div key={index} className="my-3 border-l-2 pl-3">
              <h4 className="text-xs font-medium">Turn {index + 1}</h4>
              <pre>{turn.prompt}</pre>
              {turn.terminal_error && (
                <p className="text-xs">
                  This turn failed: {turn.terminal_error.message}. The worker adds interruption
                  records or formats an invalid final response for replay; those adjustments are not
                  in this saved history.
                </p>
              )}
              <InspectionDisclosure title="Accepted history">
                <InspectionJson value={turn.history} hideEncrypted />
              </InspectionDisclosure>
            </div>
          ))
        )}
      </InspectionDisclosure>
      <InspectionDisclosure
        title={`Current turn history (${operation.request.history.length} results)`}
      >
        <InspectionJson value={operation.request.history} hideEncrypted />
      </InspectionDisclosure>
      <InspectionDisclosure title={`Available tools (${config.tool_schemas.length})`}>
        <InspectionJson value={config.tool_schemas} />
      </InspectionDisclosure>
      <InspectionDisclosure title="Model settings and limits">
        <InspectionJson
          value={{
            model_params: config.model_params,
            max_attempts: config.max_attempts,
            max_turns: config.max_turns,
            call_timeout_seconds: config.call_timeout_seconds,
          }}
        />
      </InspectionDisclosure>
    </>
  );
}

export function OperationInput({
  runId,
  operationId,
  kind,
}: {
  runId: string;
  operationId: string;
  kind: "model" | "tool";
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Inspection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  return (
    <details
      className="mt-4 rounded-md border bg-background px-4 py-3"
      onToggle={(event) => {
        if (event.target !== event.currentTarget) return;
        const expanded = event.currentTarget.open;
        setOpen(expanded);
        controller.current?.abort();
        if (!expanded) return;
        const pending = new AbortController();
        controller.current = pending;
        setData(null);
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
          });
      }}
    >
      <summary className="text-xs font-medium">Saved {kind} input</summary>
      {open &&
        (error ? (
          <p role="alert" aria-label="Input inspection error">
            {error} Close and reopen to retry.
          </p>
        ) : data ? (
          <InputContent data={data} />
        ) : (
          <p aria-live="polite">Loading saved input…</p>
        ))}
    </details>
  );
}
