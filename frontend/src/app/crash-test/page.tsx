import { CrashTestDashboard } from "../../components/crash-test-dashboard";

export const metadata = { title: "Crashboard · Applied Agent" };

export default function CrashTestPage() {
  return <CrashTestDashboard appUrl={process.env.CRASH_TEST_APP_URL ?? "http://localhost:3000"} />;
}

// Docker supplies the application link at runtime, after the frontend is built.
export const dynamic = "force-dynamic";
