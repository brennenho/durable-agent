"use client";

import { useState } from "react";
import type { CrashSnapshot } from "../lib/crash-test";
import { operationEvents, type ExecutionEvent } from "../lib/execution-events";
import { ExecutionEvents } from "./execution-events";
import { Button } from "./ui/button";

type Source = "all" | "app" | "controller";

export function CrashTestTimeline({
  snapshot,
  appUrl,
}: {
  snapshot: CrashSnapshot;
  appUrl: string;
}) {
  const [source, setSource] = useState<Source>("all");
  const session = snapshot.current;
  const hook = snapshot.scenarios.find((item) => item.name === session?.scenario)?.hook;
  const saved =
    session?.run?.operations.flatMap((operation) =>
      operationEvents(operation, session.checkpoints[operation.id]).map((event) => ({
        ...event,
        label: `App · ${event.label}`,
        source: "app" as const,
        detail: [
          `Operation ${operation.position + 1}: ${operation.tool_name ?? operation.kind}.`,
          event.detail,
        ]
          .filter(Boolean)
          .join(" "),
      })),
    ) ?? [];
  const observations: (ExecutionEvent & { source: Source })[] = [
    ...snapshot.controls,
    ...(session?.events ?? []),
  ]
    .filter(
      (event) =>
        event.kind !== "hook" ||
        [hook, "tools.result_reused", "sandbox.started"].includes(event.label),
    )
    .map((event) => ({
      id: `observed:${event.id}`,
      operationId: event.operation_id ?? "",
      at: event.at,
      label: `Crash Test · ${event.label === "tools.result_reused" ? "Saved result reused" : event.label === "sandbox.started" ? "Sandbox started" : event.label}`,
      detail: event.detail ?? event.target?.name ?? undefined,
      failed: event.kind === "fault_error" || event.kind === "kill_observed",
      source: "controller",
    }));
  const events = [...saved, ...observations]
    .filter((event) => source === "all" || event.source === source)
    .toSorted((a, b) => Date.parse(a.at) - Date.parse(b.at));

  function download() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `crash-test-${session?.id ?? "controls"}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  return (
    <section aria-labelledby="crash-timeline-heading">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="crash-timeline-heading">Execution timeline</h2>
        <div className="flex flex-wrap gap-2">
          {session?.run && (
            <Button asChild variant="outline" size="sm">
              <a href={`${appUrl}/runs/${session.run.id}`} target="_blank" rel="noreferrer">
                Inspect run in app
              </a>
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={download}>
            Download observations
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        App milestones come from saved state. Crash Test entries are controller observations,
        ordered by timestamp.
      </p>
      <div role="group" aria-label="Timeline source" className="mb-4 flex flex-wrap gap-2">
        {(
          [
            ["all", "All events"],
            ["app", "Saved state"],
            ["controller", "Crash observations"],
          ] as const
        ).map(([value, label]) => (
          <Button
            key={value}
            variant={source === value ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={source === value}
            onClick={() => setSource(value)}
          >
            {label}
          </Button>
        ))}
      </div>
      {events.length ? (
        <ExecutionEvents events={events} />
      ) : (
        <p className="text-sm text-muted-foreground">No events from this source yet.</p>
      )}
    </section>
  );
}
