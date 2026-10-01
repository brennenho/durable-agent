import { RunDetailView } from "../../../components/run-detail";

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return (
    <main className="w-full max-w-3xl pt-10 md:pt-20">
      <h1 className="sr-only">Chat</h1>
      <RunDetailView key={runId} runId={runId} />
    </main>
  );
}
