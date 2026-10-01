import Link from "next/link";
import { Plus } from "lucide-react";
import { Button } from "../../../components/ui/button";
import { RunDetailView } from "../../../components/run-detail";

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return (
    <main>
      <div className="mb-8 flex items-center justify-between gap-4">
        <h1>Agent run</h1>
        <Button asChild variant="outline" size="sm">
          <Link href="/">
            <Plus aria-hidden="true" />
            New run
          </Link>
        </Button>
      </div>
      <RunDetailView key={runId} runId={runId} />
    </main>
  );
}
