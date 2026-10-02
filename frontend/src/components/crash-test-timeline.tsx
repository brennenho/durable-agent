"use client";

import { useState } from "react";
import { Download } from "lucide-react";
import type { CrashSnapshot } from "../lib/crash-test";
import { operationEvents, type ExecutionEvent } from "../lib/execution-events";
import { ExecutionEvents } from "./execution-events";
import { Button } from "./ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

type Source = "all" | "app" | "controller";

export function CrashTestTimeline({ snapshot }: { snapshot: CrashSnapshot }) {
  const [source, setSource] = useState<Source>("all");
  const session = snapshot.current;
  const hook = snapshot.scenarios.find((item) => item.name === session?.scenario)?.hook;
  const recordedHooks = new Set(snapshot.controls.map((event) => event.hook?.event_id));
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
    ...(session?.events ?? []).filter(
      (event) => !event.hook?.event_id || !recordedHooks.has(event.hook.event_id),
    ),
  ]
    .filter(
      (event) =>
        event.kind !== "hook" ||
        [hook, "tools.result_reused", "sandbox.started"].includes(event.label),
    )
    .map((event) => {
      const emitter = event.hook?.service === "control" ? "Control" : "Tools";
      const owner = event.kind === "lifecycle" ? emitter : "Crash Test";
      const label =
        event.label === "tools.result_reused"
          ? "Saved result reused"
          : event.label === "sandbox.started"
            ? "Sandbox started"
            : event.label;
      return {
        id: `observed:${event.id}`,
        operationId: event.operation_id ?? "",
        at: event.at,
        label: `${owner} · ${label}`,
        detail: event.detail ?? event.target?.name ?? undefined,
        failed: event.kind === "fault_error" || event.kind === "kill_observed",
        source: "controller",
      };
    });
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
    <section aria-labelledby="crash-timeline-heading" className="mt-0 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="crash-timeline-heading">Execution timeline</h2>
        <Button variant="ghost" size="sm" onClick={download}>
          <Download aria-hidden="true" />
          Export JSON
        </Button>
      </div>
      <p className="mt-1 mb-4 text-xs text-muted-foreground">
        Saved app state and controller observations, in time order.
      </p>
      <Tabs value={source} onValueChange={(value) => setSource(value as Source)}>
        <TabsList aria-label="Timeline source">
          <TabsTrigger value="all" className="text-xs">
            All events
          </TabsTrigger>
          <TabsTrigger value="app" className="text-xs">
            Saved state
          </TabsTrigger>
          <TabsTrigger value="controller" className="text-xs">
            Crash Test
          </TabsTrigger>
        </TabsList>
        <TabsContent value={source}>
          {events.length ? (
            <ExecutionEvents events={events} />
          ) : (
            <p className="my-10 text-center text-sm text-muted-foreground">
              {source === "all"
                ? "Worker, sandbox, and crash observations will appear here."
                : "No events from this source."}
            </p>
          )}
        </TabsContent>
      </Tabs>
    </section>
  );
}
