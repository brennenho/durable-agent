import { cn } from "@/lib/utils";

export function StatusDot({ live = false, className }: { live?: boolean; className?: string }) {
  return (
    <span
      aria-hidden="true"
      data-slot="status-dot"
      className={cn(
        "inline-block size-1.5 shrink-0 rounded-full bg-current",
        live && "motion-safe:animate-pulse",
        className,
      )}
    />
  );
}
