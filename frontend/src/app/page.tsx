import { RunSubmission } from "../components/run-submission";

export default function Home() {
  return (
    <main>
      <h1>Durable Agent</h1>
      <p>Give the agent a task to work on in its saved workspace.</p>
      <RunSubmission />
    </main>
  );
}
