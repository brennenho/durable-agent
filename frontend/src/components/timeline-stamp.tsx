import type { ReactNode } from "react";
import { cn } from "../lib/utils";

export function TimelineStamp({
  at,
  label,
  active = false,
  failed = false,
  duration,
}: {
  at: string;
  label: string;
  active?: boolean;
  failed?: boolean;
  duration?: ReactNode;
}) {
  const date = new Date(at);
  const description = `${label}: ${date.toLocaleString()}`;
  return (
    <>
      <div className="col-start-1 row-start-1 pt-3.5 pb-2 text-right text-xs leading-4 text-muted-foreground tabular-nums">
        <time dateTime={at} title={description} aria-label={description} className="block">
          {date.toLocaleTimeString(undefined, {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hourCycle: "h23",
          })}
        </time>
        {duration && <div className="mt-0.5 text-[11px]">{duration}</div>}
      </div>
      <span aria-hidden="true" className="relative col-start-2 row-start-1 mx-auto w-px bg-border">
        <span
          className={cn(
            "absolute top-[18px] left-1/2 size-2 -translate-x-1/2 rounded-full border bg-background",
            active && "border-foreground bg-foreground",
            failed && "border-destructive bg-destructive",
          )}
        />
      </span>
    </>
  );
}
