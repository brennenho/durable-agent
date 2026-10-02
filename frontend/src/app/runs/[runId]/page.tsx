import { RunDetailView } from "../../../components/run-detail";

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return (
    <main className="flex min-h-[calc(100dvh-3.5rem)] w-full max-w-none flex-col p-0 md:min-h-dvh">
      <h1 className="sr-only">Chat</h1>
      <RunDetailView key={runId} runId={runId} />
    </main>
  );
}
