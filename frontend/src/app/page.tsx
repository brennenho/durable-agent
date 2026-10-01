import { RunSubmission } from "../components/run-submission";

export default function Home() {
  return (
    <main className="new-run-page">
      <h1 className="mb-8 text-center text-2xl font-medium sm:text-3xl">What are we working on?</h1>
      <RunSubmission />
    </main>
  );
}
