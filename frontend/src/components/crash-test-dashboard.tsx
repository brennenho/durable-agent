"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { crashRequest, type CrashSnapshot } from "../lib/crash-test";
import { Button } from "./ui/button";
import { CrashTestTimeline } from "./crash-test-timeline";
import { CrashTestScenario } from "./crash-test-scenario";
import { CrashTestContainers } from "./crash-test-containers";
import { Badge } from "./ui/badge";

export function CrashTestDashboard({ appUrl }: { appUrl: string }) {
  const [snapshot, setSnapshot] = useState<CrashSnapshot | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

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

  return (
    <main className="max-w-6xl space-y-6 py-6 sm:py-8">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b pb-6">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <h1 className="text-2xl">Crashboard</h1>
          <Badge variant="outline" className="gap-2 font-normal text-muted-foreground">
            <span
              aria-hidden="true"
              className={`size-1.5 rounded-full ${connectionError ? "bg-destructive" : snapshot ? "bg-emerald-600" : "bg-muted-foreground"}`}
            />
            {snapshot?.project ?? "Connecting…"}
          </Badge>
        </div>
        <Button asChild variant="outline" size="sm">
          <a href={appUrl} target="_blank" rel="noreferrer">
            Open app
            <ArrowUpRight aria-hidden="true" />
          </a>
        </Button>
      </header>
      {connectionError && <p role="alert">{connectionError} Showing the last observations.</p>}
      {snapshot?.services_error && (
        <p role="alert">
          Container status unavailable: {snapshot.services_error}. Last observed{" "}
          {snapshot.services_at ? new Date(snapshot.services_at).toLocaleTimeString() : "never"}.
        </p>
      )}
      {actionError && <p role="alert">{actionError}</p>}
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
        <CrashTestScenario
          scenarios={snapshot?.scenarios ?? []}
          blocked={!snapshot || blocked}
          running={running}
          cancelDisabled={pending || !!connectionError}
          onArm={(scenario, runId) => void act("scenarios", { scenario, run_id: runId })}
          onCancel={() => void act("scenarios/cancel", {})}
        />
        <CrashTestContainers
          services={snapshot?.services}
          blocked={blocked}
          stale={unavailable}
          running={running}
          onAction={(target, action) =>
            void act("containers/action", { id: target.id, started_at: target.started_at, action })
          }
        />
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
      {snapshot && <CrashTestTimeline snapshot={snapshot} appUrl={appUrl} />}
    </main>
  );
}
