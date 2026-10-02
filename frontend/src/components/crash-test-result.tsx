import { ArrowUpRight, Check, CircleDashed, X } from "lucide-react";
import { scenarioName, type CrashSession } from "../lib/crash-test";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

export function CrashTestResult({
  session,
  appUrl,
}: {
  session?: CrashSession | null;
  appUrl: string;
}) {
  const run = session?.run;
  const running = session?.status === "running";
  const status = running ? (run ? "Observing run" : "Waiting for a run") : session?.status;

  return (
    <section aria-labelledby="result-heading" className="mt-0 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="result-heading">
          {session ? scenarioName(session.scenario) : "No scenario running"}
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          {session?.manual_intervention && <Badge variant="outline">Manual intervention</Badge>}
          {status && (
            <Badge
              role="status"
              variant={status === "failed" ? "destructive" : "secondary"}
              className={status === "passed" ? "bg-emerald-50 text-emerald-800" : undefined}
            >
              {running && <CircleDashed aria-hidden="true" />}
              {status}
            </Badge>
          )}
        </div>
      </div>
      {!session && (
        <p className="mb-0 text-sm text-muted-foreground">
          Choose a scenario to watch a run recover from a crash. Baseline observes without a fault.
        </p>
      )}
      {running && !run && (
        <p className="mb-0 text-sm text-muted-foreground">
          Send a task in the app. This scenario will attach when a worker picks it up.
        </p>
      )}
      {session?.error && (
        <p role="alert" className="mb-0 text-sm">
          {session.error}. Showing the last observations.
        </p>
      )}
      {run && (
        <div className="mt-4 border-t pt-4">
          <p className="mt-0 line-clamp-3 text-sm">{run.prompt}</p>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground">
              Run {run.status} · {run.operations.length}{" "}
              {run.operations.length === 1 ? "operation" : "operations"}
            </span>
            <Button asChild variant="outline" size="sm">
              <a href={`${appUrl}/runs/${run.id}`} target="_blank" rel="noreferrer">
                Open run <ArrowUpRight aria-hidden="true" />
              </a>
            </Button>
          </div>
        </div>
      )}
      {session && Object.keys(session.checks).length > 0 && (
        <ul aria-label="Recovery checks" className="mt-4 space-y-2 border-t pt-4 pl-0">
          {Object.entries(session.checks).map(([label, passed]) => (
            <li key={label} className="flex list-none items-start gap-2 text-xs">
              {passed ? (
                <Check aria-hidden="true" className="size-4 shrink-0 text-emerald-600" />
              ) : (
                <X aria-hidden="true" className="size-4 shrink-0 text-destructive" />
              )}
              <span>
                <span className="sr-only">{passed ? "Passed" : "Failed"}: </span>
                {label.replaceAll("_", " ")}
              </span>
            </li>
          ))}
        </ul>
      )}
      {session?.observed_at && (
        <p className="mb-0 text-xs text-muted-foreground">
          Last observed{" "}
          <time dateTime={session.observed_at}>
            {new Date(session.observed_at).toLocaleTimeString()}
          </time>
        </p>
      )}
    </section>
  );
}
