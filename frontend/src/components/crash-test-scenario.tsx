"use client";

import { useState } from "react";
import { ChevronRight, Play, Square } from "lucide-react";
import { scenarioName, type CrashSnapshot } from "../lib/crash-test";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

export function CrashTestScenario({
  scenarios,
  blocked,
  running,
  activeScenario,
  cancelDisabled,
  onArm,
  onCancel,
}: {
  scenarios: CrashSnapshot["scenarios"];
  blocked: boolean;
  running: boolean;
  activeScenario?: string;
  cancelDisabled: boolean;
  onArm: (scenario: string, runId: string | null) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("baseline");
  const [runId, setRunId] = useState("");
  const selected = scenarios.find((item) => item.name === (activeScenario ?? name)) ?? scenarios[0];

  return (
    <section aria-labelledby="scenario-heading" className="mt-0 p-5">
      <h2 id="scenario-heading">Scenario</h2>
      <p className="mt-1 mb-5 text-xs text-muted-foreground">
        {running
          ? "Scenario kills restart automatically."
          : "Arm a scenario, then send a task in the app."}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (selected) onArm(selected.name, runId.trim() || null);
        }}
        className="space-y-4"
      >
        <div>
          <label htmlFor="scenario" className="sr-only">
            Scenario
          </label>
          <Select
            value={selected?.name ?? name}
            onValueChange={(value) => {
              // Radix's native form control can emit an empty value while options load.
              if (value) setName(value);
            }}
            disabled={blocked || !selected}
          >
            <SelectTrigger id="scenario" aria-describedby="scenario-description">
              <SelectValue>
                {selected ? scenarioName(selected.name) : "Loading scenarios…"}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {scenarios.map((item) => (
                <SelectItem key={item.name} value={item.name}>
                  {scenarioName(item.name)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p id="scenario-description" className="mb-0 text-sm text-muted-foreground">
            {selected?.description ?? "Waiting for the controller."}
          </p>
        </div>
        <details className="group text-xs">
          <summary className="flex items-center gap-1.5 text-muted-foreground [&::-webkit-details-marker]:hidden">
            <ChevronRight
              aria-hidden="true"
              className="size-3.5 transition-transform group-open:rotate-90"
            />
            Use an existing run
          </summary>
          <label htmlFor="run-id" className="mt-3 mb-2">
            Run ID
          </label>
          <Input
            id="run-id"
            value={runId}
            onChange={(event) => setRunId(event.target.value)}
            disabled={blocked}
            placeholder="Paste a run ID"
            pattern="[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
            className="font-mono text-xs"
          />
        </details>
        <div className="flex flex-wrap gap-2 border-t pt-4">
          <Button type="submit" disabled={blocked || !selected}>
            <Play aria-hidden="true" />
            Arm scenario
          </Button>
          {running && (
            <Button type="button" variant="outline" disabled={cancelDisabled} onClick={onCancel}>
              <Square aria-hidden="true" />
              Cancel scenario
            </Button>
          )}
        </div>
      </form>
    </section>
  );
}
