"use client";

import { useState } from "react";
import type { RunDetail } from "../lib/api";
import { SavedFiles } from "./saved-files";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

export function FileRail({ runs }: { runs: RunDetail[] }) {
  const [selectedRunId, setSelectedRunId] = useState("latest");
  const run = runs.find((item) => item.id === selectedRunId) ?? runs.at(-1);
  if (!run) return null;

  return (
    <>
      <h2 className="mb-4 text-sm font-medium">Files</h2>
      {runs.length > 1 && (
        <div className="mb-4">
          <Select value={selectedRunId} onValueChange={setSelectedRunId}>
            <SelectTrigger aria-label="File version">
              <SelectValue>
                {selectedRunId === "latest"
                  ? "Latest files"
                  : `After message ${runs.indexOf(run) + 1}`}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="latest">Latest files</SelectItem>
              {runs.map((item, index) => (
                <SelectItem key={item.id} value={item.id} textValue={`After message ${index + 1}`}>
                  <span className="block">After message {index + 1}</span>
                  <span className="mt-1 block truncate text-[11px] text-muted-foreground">
                    {item.prompt}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      <SavedFiles key={run.id} runId={run.id} workspaceVersionId={run.workspace_version_id} />
    </>
  );
}
