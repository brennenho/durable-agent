import { cn } from "../lib/utils";

export function TimelineStamp({
  at,
  label,
  active = false,
  failed = false,
}: {
  at: string;
  label: string;
  active?: boolean;
  failed?: boolean;
}) {
  const date = new Date(at);
  const description = `${label}: ${date.toLocaleString()}`;
  return (
    <>
      <time
        dateTime={at}
        title={description}
        aria-label={description}
        className="col-start-1 row-start-1 pt-3.5 text-right text-xs leading-4 text-muted-foreground tabular-nums"
      >
        {date.toLocaleTimeString(undefined, {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hourCycle: "h23",
        })}
      </time>
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
