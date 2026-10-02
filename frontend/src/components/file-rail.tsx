"use client";

import { useState } from "react";
import type { RunDetail } from "../lib/api";
import { SavedFiles } from "./saved-files";

export function FileRail({ runs }: { runs: RunDetail[] }) {
  const [selectedRunId, setSelectedRunId] = useState("");
  const run = runs.find((item) => item.id === selectedRunId) ?? runs.at(-1);
  if (!run) return null;

  return (
    <>
      <h2 className="mb-4 text-xs font-medium text-muted-foreground">Files</h2>
      {runs.length > 1 && (
        <select
          aria-label="File version"
          className="mb-3 w-full min-w-0 truncate rounded-md border bg-background px-2 py-1.5 text-xs"
          value={selectedRunId}
          onChange={(event) => setSelectedRunId(event.target.value)}
        >
          <option value="">Latest message</option>
          {runs.map((item, index) => (
            <option key={item.id} value={item.id}>
              Message {index + 1} — {item.prompt.slice(0, 50)}
            </option>
          ))}
        </select>
      )}
      <SavedFiles key={run.id} runId={run.id} workspaceVersionId={run.workspace_version_id} />
    </>
  );
}
