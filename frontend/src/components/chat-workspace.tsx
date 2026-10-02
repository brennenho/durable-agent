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
    <div className="grid flex-1 grid-cols-1 grid-rows-[1fr_auto] gap-y-8 lg:grid-cols-[minmax(0,1fr)_17rem] lg:gap-y-0">
      <div className="mx-auto w-full min-w-0 max-w-3xl px-5 pt-10 sm:px-8 md:pt-20">{children}</div>
      <aside
        aria-label="Chat files"
        className="mx-auto w-full min-w-0 max-w-3xl border-t px-5 py-6 sm:px-8 lg:sticky lg:top-0 lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:h-dvh lg:max-w-none lg:self-start lg:overflow-y-auto lg:border-t-0 lg:border-l lg:bg-muted/20 lg:px-5"
      >
        <FileRail runs={runs} />
      </aside>
      {composer && (
        <div className="sticky bottom-0 min-w-0 bg-background py-4 lg:col-start-1">
          <div className="mx-auto w-full max-w-3xl px-5 sm:px-8">{composer}</div>
        </div>
      )}
    </div>
  );
}
