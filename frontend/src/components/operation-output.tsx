import type { RunDetail } from "../lib/api";
import { InspectionDisclosure, InspectionJson, ToolArguments } from "./inspection-json";

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
        {(["stdout", "stderr"] as const).map((stream) =>
          value[stream] ? (
            <div key={stream} className="my-3">
              <h4 className="text-xs font-medium text-muted-foreground">{stream}</h4>
              <pre className="mt-2 max-h-80 overflow-auto rounded bg-muted/60 p-3">
                {String(value[stream])}
              </pre>
            </div>
          ) : null,
        )}
        {!value.stdout && !value.stderr && (
          <p className="text-xs text-muted-foreground">No command output.</p>
        )}
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

export function OperationOutput({ result }: { result: Operation["result"] }) {
  if (!result) return <p className="text-xs text-muted-foreground">No saved output yet.</p>;
  if (result.kind === "tool") return <ToolOutput result={result} />;
  return (
    <>
      {result.output.status !== "completed" && (
        <p className="text-xs">Response status: {result.output.status}</p>
      )}
      {result.output.text && (
        <pre className="my-3 max-h-80 overflow-auto">{result.output.text}</pre>
      )}
      {result.output.tool_calls.length > 0 ? (
        <>
          <h4 className="mt-4 text-xs font-medium">
            Requested tools ({result.output.tool_calls.length})
          </h4>
          <p className="text-xs text-muted-foreground">
            Requests only; execution appears as separate steps.
          </p>
          <ol className="list-none space-y-4 p-0">
            {result.output.tool_calls.map((call, index) => (
              <li key={index}>
                <p className="my-1 font-mono text-xs">{call.name}</p>
                <ToolArguments value={call.arguments} />
              </li>
            ))}
          </ol>
        </>
      ) : (
        !result.output.text && <p className="text-xs text-muted-foreground">No text returned.</p>
      )}
    </>
  );
}

export function OutputTechnical({ operation }: { operation: Operation }) {
  const { result } = operation;
  const accepted = operation.attempts.find((attempt) => attempt.status === "accepted");
  return (
    <>
      {accepted && <p className="text-xs">Accepted from attempt {accepted.attempt_no}.</p>}
      {result?.kind === "tool" && (
        <p className="run-id">
          Call ID: {result.call_id}
          <br />
          Saved workspace: {result.workspace_version_id}
        </p>
      )}
      {result?.kind === "model" && (
        <>
          {result.output.tool_calls.map((call) => (
            <p key={call.call_id} className="run-id">
              {call.name} · Call ID: {call.call_id}
            </p>
          ))}
          <InspectionDisclosure title="Raw provider output">
            <InspectionJson value={result.native_context} hideEncrypted />
          </InspectionDisclosure>
        </>
      )}
    </>
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
    <>
      <p className="run-id">Attempt ID: {attempt.id}</p>
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
    </>
  );
}
