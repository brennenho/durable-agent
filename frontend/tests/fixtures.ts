import type {
  ConversationDetail,
  ConversationSummary,
  RunDetail,
  RunSummary,
} from "../src/lib/api";

export const run: RunSummary = {
  id: "00000000-0000-4000-8000-000000000001",
  prompt: "Write a Python function and tests.",
  model: "test-model",
  status: "queued",
  pause_requested: false,
  created_at: "2026-10-01T12:00:00Z",
  finished_at: null,
  next_attempt_at: "2026-10-01T12:00:00Z",
  last_heartbeat_at: null,
  last_progress_at: null,
  terminal_error: null,
};

export const runDetail: RunDetail = {
  ...run,
  operations: [],
  final_answer: null,
  workspace_version_id: "00000000-0000-4000-8000-000000000002",
};

export const conversation: ConversationSummary = {
  id: "00000000-0000-4000-8000-000000000010",
  title: "Python function and tests",
  created_at: run.created_at,
  updated_at: run.created_at,
  status: run.status,
  pause_requested: false,
  latest_run_id: run.id,
};

export const conversationDetail: ConversationDetail = {
  ...conversation,
  runs: [runDetail],
};
