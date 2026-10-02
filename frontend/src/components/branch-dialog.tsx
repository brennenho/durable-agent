"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Dialog } from "radix-ui";
import { GitBranch, X } from "lucide-react";
import {
  ApiError,
  requestJson,
  type ConversationSummary,
  type RunDetail,
  type SubmitForkRequest,
} from "../lib/api";
import { operationName } from "./activity-state";
import { OperationInspection } from "./operation-inspection";
import { SavedFiles } from "./saved-files";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

type Operation = RunDetail["operations"][number];

export function BranchDialog({ run, operation }: { run: RunDetail; operation: Operation }) {
  const router = useRouter();
  const inputId = useId();
  const instruction = useRef<HTMLTextAreaElement>(null);
  const [open, setOpen] = useState(false);
  const [inspect, setInspect] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ prompt: string; key: string } | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const checkpoint = operation.checkpoint;
  if (!checkpoint || (run.agent && run.agent.name !== "builtin")) return null;

  function changeOpen(next: boolean) {
    if (!next && controller.current) {
      controller.current.abort();
      controller.current = null;
      setBusy(false);
      setError("Creation may have finished. Retry to open the same branch.");
    }
    setInspect(false);
    setOpen(next);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (controller.current || !prompt.trim()) return;
    if (pending.current?.prompt !== prompt) pending.current = { prompt, key: crypto.randomUUID() };
    const requestController = new AbortController();
    controller.current = requestController;
    setBusy(true);
    setError(null);
    try {
      const saved = await requestJson<ConversationSummary>(
        `/api/runs/${encodeURIComponent(run.id)}/forks`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            submission_key: pending.current.key,
            prompt,
            after_operation_id: operation.id,
          } satisfies SubmitForkRequest),
          signal: requestController.signal,
        },
      );
      if (!requestController.signal.aborted) router.push(`/chats/${encodeURIComponent(saved.id)}`);
    } catch (cause) {
      if (!requestController.signal.aborted)
        setError(
          cause instanceof ApiError
            ? cause.message
            : "Could not confirm creation. Retry to open the same branch.",
        );
    } finally {
      if (controller.current === requestController) {
        controller.current = null;
        setBusy(false);
      }
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={changeOpen}>
      <Dialog.Trigger asChild>
        <Button variant="outline" size="xs" className="justify-self-end">
          <GitBranch aria-hidden="true" /> Branch
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-foreground/20" />
        <Dialog.Content
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            instruction.current?.focus();
          }}
          className="fixed top-1/2 left-1/2 z-50 flex max-h-[85dvh] w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border bg-background shadow-lg"
        >
          <div className="flex items-center justify-between gap-3 border-b p-4">
            <Dialog.Title className="text-base font-medium">
              Branch from step {operation.position + 1}
            </Dialog.Title>
            <Dialog.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Close branch dialog">
                <X />
              </Button>
            </Dialog.Close>
          </div>
          <div className="overflow-auto p-5">
            <Dialog.Description className="mt-0 text-sm text-muted-foreground">
              Start a new chat with the context and files saved through this step. Later work is
              excluded.
            </Dialog.Description>
            <p className="text-sm font-medium">
              {operationName(operation)}{" "}
              <code className="font-normal text-muted-foreground">{operation.tool_target}</code>
            </p>
            <details
              onToggle={(event) => {
                if (event.target === event.currentTarget) setInspect(event.currentTarget.open);
              }}
            >
              <summary className="text-xs text-muted-foreground">Inspect this step</summary>
              {inspect && <OperationInspection run={run} operation={operation} />}
            </details>
            <h3 className="mt-5 mb-2 text-xs font-medium">
              Files at step {operation.position + 1}
            </h3>
            <div className="max-h-40 overflow-auto">
              <SavedFiles runId={run.id} workspaceVersionId={checkpoint.workspace_version_id} />
            </div>
            <form className="mt-5" onSubmit={submit} aria-label="Create branch" aria-busy={busy}>
              <label htmlFor={inputId} className="mb-2 text-sm">
                New instruction
              </label>
              <Textarea
                ref={instruction}
                id={inputId}
                value={prompt}
                required
                disabled={busy}
                rows={3}
                placeholder="What should the agent do differently?"
                onChange={(event) => {
                  setPrompt(event.target.value);
                  setError(null);
                }}
                aria-describedby={error ? `${inputId}-error` : undefined}
              />
              {error && (
                <p role="alert" id={`${inputId}-error`} className="text-xs">
                  {error}
                </p>
              )}
              <div className="mt-4 flex justify-end gap-2">
                <Dialog.Close asChild>
                  <Button type="button" variant="outline">
                    Cancel
                  </Button>
                </Dialog.Close>
                <Button type="submit" disabled={busy || !prompt.trim()}>
                  {busy ? "Creating…" : error ? "Retry creation" : "Create branch"}
                </Button>
              </div>
            </form>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
