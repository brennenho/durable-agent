import type { RunDetail } from "../lib/api";
import { cn } from "../lib/utils";
import { Badge } from "./ui/badge";

export function FailedAttemptsBadge({
  operations,
  className,
}: {
  operations: RunDetail["operations"];
  className?: string;
}) {
  const count = operations.reduce(
    (total, operation) =>
      total + operation.attempts.filter((attempt) => attempt.status === "error").length,
    0,
  );
  if (count === 0) return null;
  return (
    <Badge
      variant="outline"
      className={cn("border-destructive/20 bg-destructive/5 text-destructive", className)}
    >
      {count} failed {count === 1 ? "attempt" : "attempts"}
    </Badge>
  );
}
