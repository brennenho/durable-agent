import type { RunDetail } from "../lib/api";
import { InspectionDisclosure, InspectionJson } from "./inspection-json";

type Operation = RunDetail["operations"][number];

function ToolOutput({
  result,
}: {
  result: Extract<NonNullable<Operation["result"]>, { kind: "tool" }>;
}) {
  const { output } = result;
  if (output.status === "error")
    return (
      <p>
        {output.code}: {output.message}. Completed tool result.
      </p>
    );
  const value = output.value;
  if (
    typeof value.exit_code === "number" &&
    typeof value.stdout === "string" &&
    typeof value.stderr === "string"
  )
    return (
      <>
        <p className="text-xs">Exit code {value.exit_code} · Command completed.</p>
        <h4 className="text-xs font-medium">stdout</h4>
        <pre className="my-2 max-h-80 overflow-auto rounded bg-muted p-3">
          {value.stdout || "(empty)"}
        </pre>
        <h4 className="text-xs font-medium">stderr</h4>
        <pre className="my-2 max-h-80 overflow-auto rounded bg-muted p-3">
          {value.stderr || "(empty)"}
        </pre>
        {value.truncated === true && (
          <p className="text-xs text-muted-foreground">
            Logs were truncated at the configured limit.
          </p>
        )}
      </>
    );
  return typeof value.content === "string" ? (
    <pre className="my-2 max-h-80 overflow-auto">{value.content || "(empty)"}</pre>
  ) : (
    <InspectionJson value={value} />
  );
}

export function OperationOutput({ operation }: { operation: Operation }) {
  const { result } = operation;
  if (!result) return null;
  const accepted = operation.attempts.find((attempt) => attempt.status === "accepted");
  return (
    <details className="mt-4 rounded-md border bg-background px-4 py-3">
      <summary className="text-xs font-medium">Saved {result.kind} output</summary>
      {accepted && (
        <p className="text-xs text-muted-foreground">
          Accepted from attempt {accepted.attempt_no}.
        </p>
      )}
      {result.kind === "model" ? (
        <>
          <p className="text-xs">Response status: {result.output.status}</p>
          <h4 className="text-xs font-medium">Returned text</h4>
          <pre className="my-2 max-h-80 overflow-auto">
            {result.output.text || "No text returned."}
          </pre>
          <InspectionDisclosure title={`Requested tools (${result.output.tool_calls.length})`}>
            <p className="text-xs text-muted-foreground">
              Requested calls; execution is shown in the timeline.
            </p>
            <ol className="space-y-3">
              {result.output.tool_calls.map((call, index) => (
                <li key={index}>
                  <p className="text-xs">
                    {call.name} · Call ID: {call.call_id}
                  </p>
                  <InspectionJson value={call.arguments} />
                </li>
              ))}
            </ol>
          </InspectionDisclosure>
          <InspectionDisclosure title="Provider output items">
            <InspectionJson value={result.native_context} hideEncrypted />
          </InspectionDisclosure>
        </>
      ) : (
        <>
          <ToolOutput result={result} />
          <p className="text-xs text-muted-foreground">
            Saved workspace: {result.workspace_version_id}
          </p>
        </>
      )}
    </details>
  );
}

export function AttemptMetadata({
  attempt,
  kind,
}: {
  attempt: Operation["attempts"][number];
  kind: Operation["kind"];
}) {
  const duration = attempt.finished_at
    ? Math.max(0, Date.parse(attempt.finished_at) - Date.parse(attempt.started_at)) / 1000
    : null;
  return (
    <InspectionDisclosure title="Attempt details">
      <p className="text-xs">Duration: {duration === null ? "still running" : `${duration}s`}</p>
      {kind === "model" && (
        <>
          {(attempt.status === "error" || attempt.status === "unknown") && (
            <p className="text-xs">No saved model response for this attempt.</p>
          )}
          <p className="text-xs">
            Provider request ID: {attempt.provider_request_id || "unavailable"}
          </p>
          <h4 className="text-xs font-medium">Reported token usage</h4>
          {attempt.usage ? (
            <InspectionJson value={attempt.usage} />
          ) : (
            <p className="text-xs text-muted-foreground">
              Unavailable; this does not mean zero tokens.
            </p>
          )}
        </>
      )}
    </InspectionDisclosure>
  );
}
