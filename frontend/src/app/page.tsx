import { RunSubmission } from "../components/run-submission";
import { CrashTestDashboard } from "../components/crash-test-dashboard";

export function generateMetadata() {
  return { title: process.env.CRASH_TEST_URL ? "Crashboard · Applied Agent" : "Applied Agent" };
}

export default function Home() {
  if (process.env.CRASH_TEST_URL) {
    return (
      <CrashTestDashboard appUrl={process.env.CRASH_TEST_APP_URL ?? "http://localhost:3000"} />
    );
  }
  return (
    <main className="new-run-page">
      <h1 className="mb-8 text-center text-2xl font-medium sm:text-3xl">What are we working on?</h1>
      <RunSubmission />
    </main>
  );
}

// Select the homepage from runtime configuration, including in the Docker image.
export const dynamic = "force-dynamic";
