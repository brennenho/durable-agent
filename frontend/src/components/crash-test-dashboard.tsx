"use client";

import { useEffect, useState } from "react";
import { Activity, ArrowUpRight, Circle, RotateCcw, Square } from "lucide-react";
import { crashRequest, serviceName, type CrashSnapshot } from "../lib/crash-test";
import { Button } from "./ui/button";

export function CrashTestDashboard({ appUrl }: { appUrl: string }) {
  const [snapshot, setSnapshot] = useState<CrashSnapshot | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [scenario, setScenario] = useState("saved-response-lost");
  const [runId, setRunId] = useState("");

  useEffect(() => {
    if (pending) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await crashRequest<CrashSnapshot>("snapshot", undefined, controller.signal);
        if (!controller.signal.aborted) {
          setSnapshot(next);
          setConnectionError(null);
        }
      } catch (error) {
        if (!controller.signal.aborted)
          setConnectionError(error instanceof Error ? error.message : "Controller unavailable");
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 1500);
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [pending]);

  async function act(path: string, body: unknown) {
    setPending(true);
    setActionError(null);
    try {
      await crashRequest(path, body);
      // Keep actions locked until the controller confirms the resulting state.
      setSnapshot(await crashRequest<CrashSnapshot>("snapshot"));
      setConnectionError(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Action failed");
    } finally {
      setPending(false);
    }
  }

  const current = snapshot?.current;
  const running = current?.status === "running";
  const unavailable = !!connectionError || !!snapshot?.services_error;
  const blocked = pending || running || unavailable;
  const description = snapshot?.scenarios.find((item) => item.name === scenario)?.description;

  return (
    <main className="max-w-6xl space-y-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-medium tracking-[0.18em] text-muted-foreground uppercase">
            Applied Agent · resilience
          </p>
          <h1 className="flex items-center gap-3">
            <Activity aria-hidden="true" className="size-7" />
            Crashboard
          </h1>
          <p className="mb-0 text-muted-foreground">Break a process. See what survived.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="rounded-full bg-muted px-3 py-1.5 font-mono text-xs">
            {snapshot?.project ?? "Connecting…"}
          </span>
          <Button asChild variant="outline">
            <a href={appUrl} target="_blank" rel="noreferrer">
              Open application
              <ArrowUpRight aria-hidden="true" />
            </a>
          </Button>
        </div>
      </header>
      {connectionError && <p role="alert">{connectionError} Showing the last observations.</p>}
      {snapshot?.services_error && (
        <p role="alert">
          Container status unavailable: {snapshot.services_error}. Last observed{" "}
          {snapshot.services_at ? new Date(snapshot.services_at).toLocaleTimeString() : "never"}.
        </p>
      )}
      {actionError && <p role="alert">{actionError}</p>}
      <section aria-labelledby="scenario-heading" className="bg-muted/30">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="scenario-heading">Test a recovery boundary</h2>
          <span className="text-xs text-muted-foreground">
            Uses your existing containers and runs
          </span>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void act("scenarios", { scenario, run_id: runId.trim() || null });
          }}
          className="mt-4"
        >
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-0 flex-1">
              <label htmlFor="scenario" className="mb-2 text-xs">
                Scenario
              </label>
              <select
                id="scenario"
                value={scenario}
                onChange={(event) => setScenario(event.target.value)}
                disabled={!snapshot || blocked}
                className="h-10 w-full rounded-md border bg-background px-3 text-sm"
              >
                {snapshot?.scenarios.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name.replaceAll("-", " ")}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" disabled={!snapshot || blocked}>
              Arm scenario
            </Button>
            {running && (
              <Button
                type="button"
                variant="outline"
                disabled={pending || !!connectionError}
                onClick={() => void act("scenarios/cancel", {})}
              >
                Cancel scenario
              </Button>
            )}
          </div>
          <p className="mb-2 text-sm text-muted-foreground">
            {description ?? "Loading scenarios…"}
          </p>
          <details className="text-xs text-muted-foreground">
            <summary>Target an existing run instead of the next worker claim</summary>
            <label htmlFor="run-id" className="mt-3">
              Run ID (optional)
            </label>
            <input
              id="run-id"
              value={runId}
              onChange={(event) => setRunId(event.target.value)}
              disabled={blocked}
              placeholder="Paste a run UUID"
              pattern="[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
              className="mt-2 w-full rounded border bg-background p-2 font-mono"
            />
          </details>
        </form>
      </section>
      <div>
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
          <h2>Containers</h2>
          <p className="m-0 text-xs text-muted-foreground">
            Manual kills stay stopped until you restart them.
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {snapshot?.services.map((service) => (
            <article
              aria-label={service.name}
              key={service.id}
              className="min-w-0 rounded-lg border p-4"
            >
              <div className="flex items-center justify-between gap-3">
                <h3>{serviceName(service)}</h3>
                <span
                  className={`flex items-center gap-1.5 text-xs ${!service.running || service.health === "unhealthy" ? "text-red-700" : service.health === "starting" ? "text-amber-700" : "text-emerald-700"}`}
                >
                  <Circle aria-hidden="true" className="size-2 fill-current" />
                  {service.running
                    ? service.health === "none"
                      ? "running"
                      : service.health
                    : "stopped"}
                </span>
              </div>
              <p className="truncate font-mono text-xs text-muted-foreground" title={service.name}>
                {service.name}
              </p>
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                disabled={blocked || (!service.running && service.service === "sandbox")}
                aria-label={`${service.running ? "Kill" : "Start"} ${serviceName(service)}`}
                onClick={() =>
                  void act("containers/action", {
                    id: service.id,
                    started_at: service.started_at,
                    action: service.running ? "kill" : "start",
                  })
                }
              >
                {service.running ? <Square aria-hidden="true" /> : <RotateCcw aria-hidden="true" />}
                {service.running
                  ? "Kill process"
                  : service.service === "sandbox"
                    ? "Tools manages recovery"
                    : "Start container"}
              </Button>
            </article>
          ))}
        </div>
        {snapshot?.services.length === 0 && <p>No containers found for this project.</p>}
        {!snapshot && <p role="status">Connecting to the host controller…</p>}
      </div>
      {current && (
        <section aria-labelledby="result-heading">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 id="result-heading">{current.scenario.replaceAll("-", " ")}</h2>
            <strong
              role="status"
              className={`rounded-full px-3 py-1 text-xs ${current.status === "passed" ? "bg-emerald-50 text-emerald-800" : current.status === "failed" ? "bg-red-50 text-red-800" : "bg-muted"}`}
            >
              {running ? (current.run ? "Watching recovery" : "Waiting for a run") : current.status}
            </strong>
          </div>
          {running && !current.run && (
            <p>
              Start a task in the application. The scenario is waiting for a worker claim with hooks
              enabled.
            </p>
          )}
          {current.error && (
            <p role="alert">{current.error}. Last observed run state is retained.</p>
          )}
          {current.observed_at && (
            <p className="text-xs text-muted-foreground">
              Run last observed:{" "}
              <time dateTime={current.observed_at}>
                {new Date(current.observed_at).toLocaleString()}
              </time>
            </p>
          )}
          <ul className="space-y-2 pl-0">
            {Object.entries(current.checks).map(([label, passed]) => (
              <li key={label} className="list-none text-sm">
                {passed ? "✓" : "✕"} {label}
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
