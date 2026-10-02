"use client";

import { useState } from "react";
import type { RunDetail } from "../lib/api";
import { OperationInput, useOperationInput } from "./operation-input";
import { AttemptMetadata, OperationOutput, OutputTechnical } from "./operation-output";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { OperationTimeline, useToolCheckpoint } from "./operation-timeline";

type Operation = RunDetail["operations"][number];

export function OperationInspection({ run, operation }: { run: RunDetail; operation: Operation }) {
  const [tab, setTab] = useState("output");
  const input = useOperationInput(run.id, operation.id, operation.kind);
  const checkpoint = useToolCheckpoint(
    run.id,
    operation.id,
    tab === "timeline" && operation.kind === "tool" && operation.tool_name !== "delegate",
  );
  return (
    <Tabs
      value={tab}
      onValueChange={(value) => {
        setTab(value);
        if (value === "output" || value === "timeline") input.cancel();
        else input.load();
      }}
      className="min-w-0 px-2 pt-2 pb-5 sm:pl-8"
    >
      <TabsList
        variant="line"
        aria-label="Step inspection"
        className="mb-2 h-auto! min-h-10 w-full flex-wrap justify-start gap-x-3 gap-y-1 border-b p-0"
      >
        <TabsTrigger value="output" className="flex-none px-0 text-xs after:bottom-0!">
          Output
        </TabsTrigger>
        <TabsTrigger value="input" className="flex-none px-0 text-xs after:bottom-0!">
          {operation.kind === "model" ? "Context" : "Arguments"}
        </TabsTrigger>
        <TabsTrigger value="technical" className="flex-none px-0 text-xs after:bottom-0!">
          Technical details
        </TabsTrigger>
        <TabsTrigger value="timeline" className="flex-none px-0 text-xs after:bottom-0!">
          Timeline
        </TabsTrigger>
      </TabsList>
      <TabsContent value="output">
        <OperationOutput result={operation.result} />
      </TabsContent>
      <TabsContent value="input">
        <OperationInput input={input} />
      </TabsContent>
      <TabsContent value="timeline">
        <OperationTimeline operation={operation} checkpoint={checkpoint} />
      </TabsContent>
      <TabsContent value="technical">
        <p className="run-id">Operation ID: {operation.id}</p>
        <OutputTechnical operation={operation} />
        <ol className="attempt-list">
          {operation.attempts.map((attempt) => (
            <li key={attempt.id}>
              <p className="my-1">
                <strong>
                  Attempt {attempt.attempt_no}
                  {run.max_attempts ? ` of ${run.max_attempts}` : ""}
                </strong>{" "}
                · {attempt.status}
              </p>
              <p className="my-1 text-xs text-muted-foreground">
                Started{" "}
                <time dateTime={attempt.started_at}>
                  {new Date(attempt.started_at).toLocaleString()}
                </time>
                {attempt.finished_at && (
                  <>
                    {" "}
                    · Finished{" "}
                    <time dateTime={attempt.finished_at}>
                      {new Date(attempt.finished_at).toLocaleString()}
                    </time>
                  </>
                )}
              </p>
              <AttemptMetadata attempt={attempt} kind={operation.kind} />
              {attempt.error && (
                <p className="attempt-error my-2 rounded-md bg-destructive/5 px-3 py-2 text-xs">
                  {attempt.error.code}: {attempt.error.message}{" "}
                  {attempt.error.retryable ? "Retryable." : "Not retryable."}
                </p>
              )}
              {attempt.status === "unknown" && (
                <p className="my-2 text-xs text-muted-foreground">
                  No conclusive result was recorded. The worker did not report before its lease
                  expired.
                </p>
              )}
            </li>
          ))}
        </ol>
        <OperationInput input={input} technical />
      </TabsContent>
    </Tabs>
  );
}
