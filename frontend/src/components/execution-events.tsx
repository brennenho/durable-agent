import type { ExecutionEvent } from "../lib/execution-events";
import { cn } from "../lib/utils";
import { TimelineStamp } from "./timeline-stamp";

export function ExecutionEvents({
  events,
  appUrl = "",
}: {
  events: ExecutionEvent[];
  appUrl?: string;
}) {
  return (
    <ol
      aria-label="Execution milestones"
      className="grid list-none grid-cols-[3.75rem_0.5rem_minmax(0,1fr)] gap-x-2 p-0 sm:gap-x-3"
    >
      {events.map((event) => (
        <li key={event.id} className="col-span-3 grid grid-cols-subgrid" data-event-id={event.id}>
          <TimelineStamp at={event.at} label={event.label} failed={event.failed} />
          <div className="min-w-0 py-3.5 text-xs">
            {event.runId && event.runName && (
              <a
                href={`${appUrl}/runs/${encodeURIComponent(event.runId)}`}
                target={appUrl ? "_blank" : undefined}
                rel={appUrl ? "noreferrer" : undefined}
                className="mb-1 block text-muted-foreground underline underline-offset-4 wrap-anywhere"
              >
                {event.runName}
              </a>
            )}
            <span className={cn("font-medium", event.failed && "text-destructive")}>
              {event.label}
            </span>
            {event.detail && <p className="my-1 text-xs text-muted-foreground">{event.detail}</p>}
            {event.workspaceVersionId && (
              <p className="run-id my-1">
                {event.workspaceLabel ?? "Accepted workspace"}: {event.workspaceVersionId}
              </p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
