import type { RunDetail } from "./api";

export function groupIsPausing(run: RunDetail) {
  return Boolean(
    run.pause_requested &&
    (run.status === "running" || run.children?.some((child) => child.status === "running")),
  );
}

export function subagentProgress(run: RunDetail) {
  const children = run.children ?? [];
  const finished = children.filter(
    (child) => child.status === "succeeded" || child.status === "failed",
  );
  return `${finished.length}/${children.length} finished`;
}
