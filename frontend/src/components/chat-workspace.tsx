import type { ReactNode } from "react";
import type { RunDetail } from "../lib/api";
import { FileRail } from "./file-rail";

export function ChatWorkspace({
  children,
  runs,
  composer,
}: {
  children: ReactNode;
  runs: RunDetail[];
  composer?: ReactNode;
}) {
  return (
    <div className="grid flex-1 grid-cols-1 grid-rows-[1fr_auto] gap-x-10 gap-y-8 xl:grid-cols-[minmax(0,1fr)_14rem]">
      <div className="min-w-0">{children}</div>
      <aside
        aria-label="Chat files"
        className="min-w-0 border-t pt-5 xl:sticky xl:top-8 xl:col-start-2 xl:row-span-2 xl:row-start-1 xl:max-h-[calc(100dvh-4rem)] xl:self-start xl:overflow-y-auto xl:border-t-0 xl:border-l xl:pt-0 xl:pl-5"
      >
        <FileRail runs={runs} />
      </aside>
      {composer && (
        <div className="sticky bottom-0 min-w-0 bg-background py-4 xl:col-start-1">{composer}</div>
      )}
    </div>
  );
}
