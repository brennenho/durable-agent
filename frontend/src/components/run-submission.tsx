"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";
import { ApiError, requestJson, type RunSummary, type SubmitRunRequest } from "../lib/api";

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
      <section aria-labelledby="submitted-heading">
        <h2 id="submitted-heading">Run submitted</h2>
        <p role="status">Status: {run.status}</p>
        <p className="run-id">Run ID: {run.id}</p>
        <p>
          <Link href={`/runs/${encodeURIComponent(run.id)}`}>View run</Link>
        </p>
        <button
          onClick={() => {
            pending.current = null;
            setRun(null);
            setPrompt("");
          }}
        >
          New run
        </button>
      </section>
    );
  }

  return (
    <section aria-labelledby="submit-heading">
      <h2 id="submit-heading">Start a run</h2>
      <form onSubmit={submit} aria-busy={busy}>
        <label htmlFor="prompt">What should the agent do?</label>
        <textarea
          id="prompt"
          name="prompt"
          rows={6}
          required
          disabled={busy}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          aria-describedby={error ? "submission-error" : undefined}
          placeholder="Write a Python function and tests in the workspace…"
        />
        {error && (
          <p id="submission-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy}>
          {busy ? "Submitting…" : error ? "Try again" : "Start run"}
        </button>
      </form>
    </section>
  );
}
