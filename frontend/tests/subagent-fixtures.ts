import type { RunDetail } from "../src/lib/api";
import { conversationDetail, runDetail } from "./fixtures";

export const delegation: RunDetail["operations"][number] = {
  id: "00000000-0000-4000-8000-000000000031",
  position: 0,
  kind: "tool",
  tool_name: "delegate",
  created_at: runDetail.created_at,
  completed_at: null,
  result: null,
  attempts: [],
};

export const parentRun = {
  ...runDetail,
  status: "waiting",
  parent: null,
  next_attempt_at: null,
  operations: [delegation],
  children: ["Review implementation", "Review tests", "Review docs"].map((name, child_index) => ({
    ...runDetail,
    id: `00000000-0000-4000-8000-00000000004${child_index}`,
    name,
    status: "running",
    parent_operation_id: delegation.id,
    child_index,
  })),
} satisfies RunDetail;

export const childRun: RunDetail = {
  ...runDetail,
  ...parentRun.children[0],
  prompt: "Inspect the implementation and return a report.",
  children: [],
  parent: {
    run_id: parentRun.id,
    conversation_id: conversationDetail.id,
    operation_id: delegation.id,
    name: parentRun.children[0].name,
  },
  operations: [
    {
      ...delegation,
      kind: "model",
      tool_name: null,
      completed_at: runDetail.created_at,
      checkpoint: { workspace_version_id: runDetail.workspace_version_id },
    },
  ],
};
