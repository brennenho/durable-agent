import Link from "next/link";
import { RunDetailView } from "../../../components/run-detail";

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return (
    <main>
      <Link href="/">New run</Link>
      <h1>Agent run</h1>
      <RunDetailView key={runId} runId={runId} />
    </main>
  );
}
