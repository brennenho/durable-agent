"use client";

import { useState } from "react";

type History = { ids: Set<string>; error: string | null };

function readHistory(key: string, sessionId: string | null): History {
  try {
    const value = sessionStorage.getItem(key);
    if (!value) return { ids: new Set(), error: null };
    const saved = JSON.parse(value) as { sessionId?: unknown; ids?: unknown } | null;
    if (!saved || saved.sessionId !== sessionId) return { ids: new Set(), error: null };
    if (!Array.isArray(saved.ids) || !saved.ids.every((id) => typeof id === "string"))
      throw new Error("Invalid saved timeline view");
    return { ids: new Set(saved.ids), error: null };
  } catch {
    return { ids: new Set(), error: "Could not restore this tab’s cleared timeline view." };
  }
}

export function useTimelineHistory(project: string, sessionId: string | null) {
  const key = `crashboard:timeline:${project}`;
  // The timeline mounts after the first client fetch and remounts for each scenario.
  const [history, setHistory] = useState(() => readHistory(key, sessionId));

  function save(ids: Set<string>) {
    let error: string | null = null;
    try {
      sessionStorage.setItem(key, JSON.stringify({ sessionId, ids: [...ids] }));
    } catch {
      error = "This timeline view could not be saved for reloads.";
    }
    setHistory({ ids, error });
  }

  return {
    hiddenIds: history.ids,
    storageError: history.error,
    clear: (ids: string[]) => save(new Set([...history.ids, ...ids])),
    restore: () => save(new Set()),
  };
}
