import { CrashTestDashboard } from "../../components/crash-test-dashboard";

export const metadata = { title: "Crashboard · Applied Agent" };

export default function CrashTestPage() {
  return <CrashTestDashboard appUrl={process.env.CRASH_TEST_APP_URL ?? "http://localhost:3000"} />;
}
