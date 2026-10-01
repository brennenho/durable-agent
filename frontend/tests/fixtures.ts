import type { RunSummary } from "../src/lib/api";

export const run: RunSummary = {
  id: "00000000-0000-4000-8000-000000000001",
  prompt: "Write a Python function and tests.",
  model: "test-model",
  status: "queued",
  created_at: "2026-10-01T12:00:00Z",
  finished_at: null,
  next_attempt_at: "2026-10-01T12:00:00Z",
  last_heartbeat_at: null,
  last_progress_at: null,
  terminal_error: null,
};
