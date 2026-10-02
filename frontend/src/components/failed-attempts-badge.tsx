import type { RunDetail } from "../lib/api";
import { cn } from "../lib/utils";
import { Badge } from "./ui/badge";

export function FailedAttemptsBadge({
  operations,
  compact = false,
}: {
  operations: RunDetail["operations"];
  compact?: boolean;
}) {
  const count = operations.reduce(
    (total, operation) =>
      total + operation.attempts.filter((attempt) => attempt.status === "error").length,
    0,
  );
  if (count === 0) return null;
  const description = `failed ${count === 1 ? "attempt" : "attempts"}`;
  return (
    <Badge
      variant="outline"
      title={`${count} ${description}`}
      className={cn(
        "border-destructive/20 bg-destructive/5 text-destructive",
        compact && "h-5 min-w-5 gap-0 px-1 py-0 tabular-nums",
      )}
    >
      {count}
      <span className={compact ? "sr-only" : undefined}> {description}</span>
    </Badge>
  );
}
