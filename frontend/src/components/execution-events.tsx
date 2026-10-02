import type { ExecutionEvent } from "../lib/execution-events";
import { cn } from "../lib/utils";

export function ExecutionEvents({ events }: { events: ExecutionEvent[] }) {
  return (
    <ol aria-label="Execution milestones" className="ml-1 list-none border-l p-0 pl-4">
      {events.map((event) => (
        <li key={event.id} className="relative py-2" data-event-id={event.id}>
          <span
            aria-hidden="true"
            className={cn(
              "absolute top-4 -left-[1.3rem] size-2 rounded-full border bg-background",
              event.failed && "border-destructive bg-destructive",
            )}
          />
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-xs">
            <span className={cn("font-medium", event.failed && "text-destructive")}>
              {event.label}
            </span>
            <time dateTime={event.at} className="text-muted-foreground tabular-nums">
              {new Date(event.at).toLocaleString()}
            </time>
          </div>
          {event.detail && <p className="my-1 text-xs text-muted-foreground">{event.detail}</p>}
          {event.workspaceVersionId && (
            <p className="run-id my-1">
              {event.workspaceLabel ?? "Accepted workspace"}: {event.workspaceVersionId}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}
