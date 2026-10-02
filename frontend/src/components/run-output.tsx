import type { RunDetail } from "../lib/api";
import { InspectionJson } from "./inspection-json";
import { MarkdownAnswer } from "./markdown-answer";

export function RunOutput({ run }: { run: RunDetail }) {
  let content;
  if (run.agent) {
    if (run.status !== "succeeded") return null;
    // SDK output is authoritative, including JSON null and empty values.
    content =
      run.output === undefined ? (
        <p className="text-muted-foreground">The saved output is unavailable.</p>
      ) : typeof run.output === "string" && run.output.trim() ? (
        <MarkdownAnswer text={run.output} />
      ) : (
        <InspectionJson value={run.output} />
      );
  } else {
    const finished = run.status === "succeeded" || run.status === "failed";
    if (run.final_answer === null && (!finished || run.terminal_error)) return null;
    content = run.final_answer ? (
      <MarkdownAnswer text={run.final_answer} />
    ) : (
      <p className="text-muted-foreground">No final answer.</p>
    );
  }
  return (
    <section aria-label="Final output" className="m-0 border-0 bg-transparent p-0">
      {content}
    </section>
  );
}
