"use client";

import { useState } from "react";
import { Download } from "lucide-react";
import { scenarioName, type CrashSnapshot } from "../lib/crash-test";
import { operationEvents, type ExecutionEvent } from "../lib/execution-events";
import { ExecutionEvents } from "./execution-events";
import { Button } from "./ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { StatusDot } from "./ui/status-dot";

type Source = "all" | "app" | "controller";
type Scope = "scenario" | "activity";
type Props = { snapshot: CrashSnapshot; appUrl: string; live: boolean };

export function CrashTestTimeline(props: Props) {
  // A newly armed scenario starts with its own events and fresh filters.
  return (
    <Timeline
      key={`${props.snapshot.project}:${props.snapshot.current?.id ?? "activity"}`}
      {...props}
    />
  );
}

function Timeline({ snapshot, appUrl, live }: Props) {
  const session = snapshot.current;
  const [scope, setScope] = useState<Scope>(session ? "scenario" : "activity");
  const [source, setSource] = useState<Source>("all");
  const [selectedRun, setSelectedRun] = useState("all");
  const runs = session?.run ? [session.run, ...(session.child_runs ?? [])] : [];
  const runFilter = runs.some((run) => run.id === selectedRun) ? selectedRun : "all";
  const runNames = new Map(runs.map((run) => [run.id, run.parent?.name ?? "Parent agent"]));
  const operationRuns = new Map(
    runs.flatMap((run) => run.operations.map((operation) => [operation.id, run.id] as const)),
  );
  const hook = snapshot.scenarios.find((item) => item.name === session?.scenario)?.hook;
  const sessionHooks = new Set(session?.events.map((event) => event.hook?.event_id));
  const controls = snapshot.controls.filter(
    (event) =>
      scope === "activity" ||
      (event.operation_id && operationRuns.has(event.operation_id)) ||
      (event.hook?.event_id && sessionHooks.has(event.hook.event_id)),
  );
  const recordedHooks = new Set(controls.map((event) => event.hook?.event_id));
  const saved = runs.flatMap((run) =>
    run.operations.flatMap((operation) =>
      operationEvents(operation, session?.checkpoints[operation.id], run.children).map((event) => ({
        ...event,
        runId: event.runId ?? run.id,
        runName: event.runName ?? (runs.length > 1 ? runNames.get(run.id) : undefined),
        label: `App · ${event.label}`,
        source: "app" as const,
        detail: [
          `Operation ${operation.position + 1}: ${operation.tool_name ?? operation.kind}.`,
          event.detail,
        ]
          .filter(Boolean)
          .join(" "),
      })),
    ),
  );
  const observations: (ExecutionEvent & { source: Source })[] = [
    ...controls,
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
        runId: event.operation_id ? operationRuns.get(event.operation_id) : undefined,
        runName:
          runs.length > 1 && event.operation_id
            ? runNames.get(operationRuns.get(event.operation_id) ?? "")
            : undefined,
        at: event.at,
        label: `${owner} · ${label}`,
        detail: event.detail ?? event.target?.name ?? undefined,
        failed: event.kind === "fault_error" || event.kind === "kill_observed",
        source: "controller",
      };
    });
  const events = [...saved, ...observations]
    .filter((event) => source === "all" || event.source === source)
    .filter((event) => runFilter === "all" || event.runId === runFilter)
    .toSorted((a, b) => Date.parse(a.at) - Date.parse(b.at));

  function download() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify({ ...snapshot, controls }, null, 2)], { type: "application/json" }),
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
      <div className="my-4 flex flex-wrap items-center gap-3">
        <Select value={scope} onValueChange={(value) => setScope(value as Scope)}>
          <SelectTrigger aria-label="Timeline view" className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="scenario" disabled={!session}>
              This scenario
            </SelectItem>
            <SelectItem value="activity">All activity</SelectItem>
          </SelectContent>
        </Select>
        <p className="m-0 flex items-center gap-2 text-xs text-muted-foreground">
          {scope === "scenario" && session ? (
            <>
              <StatusDot
                live={live && session.status === "running"}
                className={
                  session.status === "running" ? "text-amber-600" : "text-muted-foreground"
                }
              />
              {scenarioName(session.scenario)} ·{" "}
              {session.status === "running"
                ? session.run
                  ? "Running"
                  : "Waiting for a run"
                : `Finished · ${session.status}`}
            </>
          ) : (
            "Controller history and the selected run."
          )}
        </p>
      </div>
      <Tabs value={source} onValueChange={(value) => setSource(value as Source)}>
        <div className="flex flex-wrap items-center gap-3">
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
          {runs.length > 1 && (
            <select
              aria-label="Timeline run"
              value={runFilter}
              onChange={(event) => setSelectedRun(event.target.value)}
              className="min-w-0 max-w-full rounded-md border bg-background px-2 py-1 text-xs"
            >
              <option value="all">All agents</option>
              {runs.map((run) => (
                <option key={run.id} value={run.id}>
                  {runNames.get(run.id)}
                </option>
              ))}
            </select>
          )}
        </div>
        <TabsContent value={source}>
          {events.length ? (
            <ExecutionEvents events={events} appUrl={appUrl} />
          ) : (
            <p className="my-10 text-center text-sm text-muted-foreground">
              {source === "all"
                ? scope === "scenario"
                  ? "This scenario’s events will appear here."
                  : "Worker, sandbox, and crash observations will appear here."
                : "No events from this source."}
            </p>
          )}
        </TabsContent>
      </Tabs>
    </section>
  );
}
