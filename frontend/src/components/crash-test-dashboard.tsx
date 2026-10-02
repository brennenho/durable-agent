"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { crashRequest, type CrashSnapshot } from "../lib/crash-test";
import { Button } from "./ui/button";
import { CrashTestTimeline } from "./crash-test-timeline";
import { CrashTestScenario } from "./crash-test-scenario";
import { CrashTestContainers } from "./crash-test-containers";
import { Badge } from "./ui/badge";
import { CrashTestResult } from "./crash-test-result";
import { StatusDot } from "./ui/status-dot";

export function CrashTestDashboard({ appUrl }: { appUrl: string }) {
  const [snapshot, setSnapshot] = useState<CrashSnapshot | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const refreshSnapshot = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await crashRequest<CrashSnapshot>("snapshot", undefined, signal);
      if (!signal?.aborted) {
        setSnapshot(next);
        setConnectionError(null);
      }
    } catch (error) {
      if (!signal?.aborted)
        setConnectionError(error instanceof Error ? error.message : "Controller unavailable");
    }
  }, []);

  useEffect(() => {
    if (pending) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      await refreshSnapshot(controller.signal);
      if (!controller.signal.aborted) timer = setTimeout(poll, 1500);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [pending, refreshSnapshot]);

  async function act(path: string, body: unknown) {
    setPending(true);
    setActionError(null);
    try {
      await crashRequest(path, body);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Action failed");
    }
    // A failed refresh retains observations and locks controls until polling recovers.
    await refreshSnapshot();
    setPending(false);
  }

  const current = snapshot?.current;
  const running = current?.status === "running";
  const unavailable = !!connectionError || !!snapshot?.services_error;
  const blocked = pending || unavailable;
  const live = !!snapshot && !connectionError && !pending;

  return (
    <main className="max-w-6xl space-y-6 py-6 sm:py-8">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b pb-6">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <h1 className="text-2xl">Crashboard</h1>
          <Badge variant="outline" className="gap-2 font-normal text-muted-foreground">
            <StatusDot
              live={live}
              className={
                connectionError
                  ? "text-destructive"
                  : snapshot
                    ? "text-emerald-600"
                    : "text-muted-foreground"
              }
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
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-5 lg:col-start-2 lg:row-start-1">
          <CrashTestScenario
            scenarios={snapshot?.scenarios ?? []}
            blocked={!snapshot || running || blocked}
            running={running}
            activeScenario={running ? current?.scenario : undefined}
            cancelDisabled={pending || !!connectionError}
            onArm={(scenario, runId) => void act("scenarios", { scenario, run_id: runId })}
            onCancel={() => void act("scenarios/cancel", {})}
          />
          <CrashTestContainers
            services={snapshot?.services}
            blocked={blocked}
            stale={unavailable}
            live={live}
            onAction={(target, action) =>
              void act("containers/action", {
                id: target.id,
                started_at: target.started_at,
                action,
              })
            }
          />
          <section className="space-y-2 border p-4" aria-label="Worker autoscaling">
            <div className="flex items-center justify-between gap-3">
              <h2>Worker autoscaling</h2>
              <Button
                variant="outline"
                size="sm"
                role="switch"
                aria-label="Allow autoscaling"
                aria-checked={snapshot?.autoscaling_enabled ?? false}
                disabled={!snapshot || pending || !!connectionError}
                onClick={() => void act("autoscaling", { enabled: !snapshot?.autoscaling_enabled })}
              >
                {!snapshot ? "Loading…" : snapshot.autoscaling_enabled ? "Allowed" : "Paused"}
              </Button>
            </div>
            <p className="text-sm text-muted-foreground">
              Applies at the next scaling check. Existing workers keep running.
            </p>
            <p className="text-xs text-muted-foreground">
              {snapshot?.autoscaling_checked_at
                ? `Last check: ${new Date(snapshot.autoscaling_checked_at).toLocaleTimeString()}`
                : "Waiting for Control. Enable Crash Test hooks in the app."}
            </p>
          </section>
        </div>
        <div className="min-w-0 space-y-5 lg:col-start-1 lg:row-start-1">
          <CrashTestResult session={current} appUrl={appUrl} />
          {snapshot && <CrashTestTimeline snapshot={snapshot} appUrl={appUrl} live={live} />}
        </div>
      </div>
    </main>
  );
}
