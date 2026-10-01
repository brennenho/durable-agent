"use client";

import { useEffect, useState } from "react";
import { ApiError, requestJson, type RunDetail } from "../lib/api";
import { RunHistory } from "./run-history";
import { SavedFiles } from "./saved-files";
import { Badge } from "./ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

export function RunDetailView({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      let retry = true;
      try {
        const latest = await requestJson<RunDetail>(`/api/runs/${encodeURIComponent(runId)}`, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setRun(latest);
        setError(null);
        retry = latest.status !== "succeeded" && latest.status !== "failed";
      } catch (cause) {
        if (controller.signal.aborted) return;
        const permanent =
          cause instanceof ApiError &&
          cause.status >= 400 &&
          cause.status < 500 &&
          cause.status !== 408 &&
          cause.status !== 429;
        const message = cause instanceof ApiError ? cause.message : "Could not refresh this run.";
        setError(permanent ? message : `${message} Retrying…`);
        retry = !permanent;
      }
      if (retry) timer = setTimeout(refresh, 2_000);
    }
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId]);

  return (
    <div className="space-y-6">
      {error && (
        <p
          role="alert"
          aria-label="Run update error"
          className="rounded-md border border-destructive/20 bg-destructive/5 p-4"
        >
          {error}
        </p>
      )}
      {!run && !error && <p role="status">Loading run…</p>}
      {run && (
        <>
          <section aria-labelledby="run-heading" className="m-0 border-0 bg-transparent p-0">
            <div className="flex items-center gap-3">
              <h2 id="run-heading">Run details</h2>
              <Badge
                role="status"
                variant={run.status === "failed" ? "destructive" : "outline"}
                className="capitalize"
              >
                <span className="sr-only">Status: </span>
                {run.status}
              </Badge>
            </div>
            <p className="run-id">Run ID: {run.id}</p>
            <p className="text-xs text-muted-foreground">
              <span className="font-mono">{run.model}</span> · Created{" "}
              <time dateTime={run.created_at}>{new Date(run.created_at).toLocaleString()}</time>
            </p>
            <p className="preserve-whitespace my-5 border-l-2 pl-4">{run.prompt}</p>
            {run.status === "retrying" && <p>The last attempt will be retried.</p>}
            {run.terminal_error && (
              <p
                role="alert"
                aria-label="Run failure"
                className="rounded-md border border-destructive/20 bg-destructive/5 p-4"
              >
                {run.terminal_error.message}
              </p>
            )}
          </section>
          <Tabs defaultValue="result">
            <TabsList variant="line" className="w-full justify-start border-b">
              <TabsTrigger value="result" className="flex-none px-4">
                Result
              </TabsTrigger>
              <TabsTrigger value="history" className="flex-none px-4">
                History
              </TabsTrigger>
            </TabsList>
            <TabsContent value="result" forceMount className="data-[state=inactive]:hidden">
              <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
                <section aria-labelledby="output-heading" className="min-w-0 min-h-64">
                  <h2 id="output-heading">Final output</h2>
                  {run.final_answer !== null ? (
                    <pre className="mt-5">
                      {run.final_answer || "The agent returned an empty answer."}
                    </pre>
                  ) : (
                    <p className="mt-5 text-muted-foreground">
                      {run.status === "failed" || run.status === "succeeded"
                        ? "This run ended without a final answer."
                        : "The final answer will appear here when the run completes."}
                    </p>
                  )}
                </section>
                <SavedFiles runId={run.id} workspaceVersionId={run.workspace_version_id} />
              </div>
            </TabsContent>
            <TabsContent value="history" forceMount className="data-[state=inactive]:hidden">
              <RunHistory operations={run.operations} status={run.status} />
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}
