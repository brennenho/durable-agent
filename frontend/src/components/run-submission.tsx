"use client";

import Link from "next/link";
import { ArrowRight, Check, LoaderCircle } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import { ApiError, requestJson, type RunSummary, type SubmitRunRequest } from "../lib/api";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

export function RunSubmission() {
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<RunSummary | null>(null);
  const pending = useRef<SubmitRunRequest | null>(null);
  const submitting = useRef(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    if (!prompt.trim()) {
      setError("Enter a task for the agent.");
      return;
    }
    if (pending.current?.prompt !== prompt) {
      pending.current = { submission_key: crypto.randomUUID(), prompt };
    }
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      const saved = await requestJson<RunSummary>("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pending.current),
      });
      setRun(saved);
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Could not confirm submission. Retry to check the same request.",
      );
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  if (run) {
    return (
      <section aria-labelledby="submitted-heading" className="submission-panel">
        <Check aria-hidden="true" className="mb-4 size-5 text-primary" />
        <h2 id="submitted-heading">Run submitted</h2>
        <p role="status">Status: {run.status}</p>
        <p className="run-id">Run ID: {run.id}</p>
        <div className="mt-6 flex items-center gap-3">
          <Button asChild>
            <Link href={`/runs/${encodeURIComponent(run.id)}`}>View run</Link>
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              pending.current = null;
              setRun(null);
              setPrompt("");
            }}
          >
            New run
          </Button>
        </div>
      </section>
    );
  }

  return (
    <section aria-labelledby="submit-heading" className="submission-panel">
      <h2 id="submit-heading" className="mb-6">
        Start a run
      </h2>
      <form onSubmit={submit} aria-busy={busy}>
        <label htmlFor="prompt">What should the agent do?</label>
        <Textarea
          className="mt-3 min-h-48 resize-y border-0 bg-transparent p-0 text-base shadow-none md:text-base"
          id="prompt"
          name="prompt"
          rows={6}
          required
          disabled={busy}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          aria-describedby={error ? "submission-error" : "prompt-help"}
          placeholder="Describe a coding or document task…"
        />
        {error && (
          <p id="submission-error" role="alert">
            {error}
          </p>
        )}
        <div className="mt-6 flex flex-wrap items-center justify-between gap-4 border-t pt-5">
          <p id="prompt-help" className="m-0 text-xs text-muted-foreground">
            Files are saved as the agent works.
          </p>
          <Button type="submit" disabled={busy}>
            {busy && <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" />}
            {busy ? "Submitting…" : error ? "Try again" : "Start run"}
            {!busy && <ArrowRight aria-hidden="true" />}
          </Button>
        </div>
      </form>
    </section>
  );
}
