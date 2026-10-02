import { RotateCcw, Square } from "lucide-react";
import { serviceName, type Container } from "../lib/crash-test";
import { cn } from "../lib/utils";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { StatusDot } from "./ui/status-dot";

export function CrashTestContainers({
  services,
  blocked,
  stale,
  live,
  onAction,
}: {
  services?: Container[];
  blocked: boolean;
  stale: boolean;
  live: boolean;
  onAction: (target: Container, action: "kill" | "start") => void;
}) {
  return (
    <section aria-labelledby="containers-heading" className="mt-0 min-w-0 overflow-hidden p-0">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-5 py-4">
        <h2 id="containers-heading">Containers</h2>
        <span className="text-xs text-muted-foreground">
          {stale
            ? "Last known state"
            : services &&
              `${services.filter((service) => service.running).length} of ${services.length} running`}
        </span>
      </div>
      <ul className="m-0 divide-y p-0">
        {services?.map((service) => {
          const stopped = !service.running;
          const status = stopped
            ? "stopped"
            : service.health === "none"
              ? "running"
              : service.health;
          return (
            <li key={service.id} className="list-none">
              <article aria-label={service.name} className="flex items-center gap-3 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <h3>{serviceName(service)}</h3>
                  <p
                    className="my-0.5 truncate font-mono text-[11px] text-muted-foreground"
                    title={service.name}
                  >
                    {service.name}
                  </p>
                </div>
                <Badge variant="ghost" className="gap-1.5 px-0 font-normal text-muted-foreground">
                  <StatusDot
                    live={live && !stale}
                    className={cn(
                      !stale &&
                        (stopped || status === "unhealthy"
                          ? "text-destructive"
                          : status === "starting"
                            ? "text-amber-600"
                            : "text-emerald-600"),
                    )}
                  />
                  {status}
                </Badge>
                <Button
                  variant="outline"
                  size="icon-sm"
                  disabled={blocked || (stopped && service.service === "sandbox")}
                  aria-label={`${stopped ? "Start" : "Kill"} ${serviceName(service)}`}
                  title={
                    stopped && service.service === "sandbox"
                      ? "Tools manages sandbox recovery"
                      : stopped
                        ? "Start container"
                        : "Kill process"
                  }
                  onClick={() => onAction(service, stopped ? "start" : "kill")}
                >
                  {stopped ? <RotateCcw aria-hidden="true" /> : <Square aria-hidden="true" />}
                </Button>
              </article>
            </li>
          );
        })}
      </ul>
      {!services && (
        <p role="status" className="px-5 text-sm text-muted-foreground">
          Loading containers…
        </p>
      )}
      {services?.length === 0 && (
        <p className="px-5 text-sm text-muted-foreground">No containers found.</p>
      )}
      <p className="m-0 border-t bg-muted/30 px-5 py-3 text-xs text-muted-foreground">
        Autoscaling may restart killed workers.
      </p>
    </section>
  );
}
