import { RunSubmission } from "../components/run-submission";

export default function Home() {
  return (
    <main className="new-run-page">
      <p className="mb-2 text-xs font-medium uppercase tracking-widest text-muted-foreground">
        Workspace
      </p>
      <h1>Start a run</h1>
      <p className="mt-3 text-muted-foreground">
        Describe the task. Follow its progress and inspect the saved results.
      </p>
      <RunSubmission />
    </main>
  );
}
