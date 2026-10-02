import Link from "next/link";
import type { RunDetail } from "../lib/api";

export function SubagentList({ runs }: { runs: NonNullable<RunDetail["children"]> }) {
  if (!runs.length) return null;
  return (
    <ul aria-label="Subagents" className="m-0 list-none divide-y rounded-md border p-0 text-xs">
      {runs.map((child) => (
        <li key={child.id} className="min-w-0 px-3 py-2.5">
          <div className="flex items-baseline justify-between gap-3">
            <Link
              href={`/runs/${encodeURIComponent(child.id)}`}
              className="min-w-0 font-medium underline underline-offset-4 wrap-anywhere"
            >
              {child.name}
            </Link>
            <span className="shrink-0 text-muted-foreground">
              {child.status === "succeeded"
                ? "Completed"
                : child.status === "failed"
                  ? "Failed"
                  : child.pause_requested
                    ? child.status === "running"
                      ? "Pausing…"
                      : "Paused"
                    : child.status === "retrying"
                      ? "Retrying"
                      : child.status === "running"
                        ? "Running"
                        : "Queued"}
            </span>
          </div>
          {child.terminal_error && (
            <p className="mb-0 mt-1 text-destructive wrap-anywhere">
              {child.terminal_error.message}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}
