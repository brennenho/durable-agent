"use client";

import { useEffect, useRef, useState } from "react";
import { Download, FileText, LoaderCircle } from "lucide-react";
import { request, requestJson, type RunFileListing } from "../lib/api";
import { Button } from "./ui/button";

export function SavedFiles({
  runId,
  workspaceVersionId,
}: {
  runId: string;
  workspaceVersionId: string;
}) {
  const [listing, setListing] = useState<RunFileListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const downloadController = useRef<AbortController | null>(null);
  const base = `/api/runs/${encodeURIComponent(runId)}`;

  useEffect(() => {
    const controller = new AbortController();
    requestJson<RunFileListing>(`${base}/files`, { signal: controller.signal })
      .then((files) => {
        if (!controller.signal.aborted) {
          setListing(files);
          setError(null);
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted)
          setError(cause instanceof Error ? cause.message : "Could not load saved files.");
      });
    return () => {
      controller.abort();
    };
  }, [base, workspaceVersionId, retry]);

  useEffect(() => () => downloadController.current?.abort(), []);

  async function download(path: string) {
    if (!listing || downloadController.current) return;
    const controller = new AbortController();
    downloadController.current = controller;
    const { signal } = controller;
    setDownloading(path);
    setDownloadError(null);
    try {
      const query = new URLSearchParams({ version_id: listing.workspace_version_id, path });
      const response = await request(`${base}/file?${query}`, { signal });
      const blob = await response.blob();
      if (signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = path.split("/").pop() || "download";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (cause) {
      if (!signal.aborted)
        setDownloadError(cause instanceof Error ? cause.message : "Download failed.");
    } finally {
      downloadController.current = null;
      if (!signal.aborted) setDownloading(null);
    }
  }

  const files = listing?.entries.filter((entry) => entry.kind === "file");
  if (files?.length === 0 && !error && !downloadError && !downloading) return null;

  return (
    <section aria-label="Saved files" className="mt-5 min-w-0 border-0 bg-transparent p-0">
      <h2 className="sr-only">Saved files</h2>
      {!listing && !error && <p className="text-xs text-muted-foreground">Loading files…</p>}
      {error && (
        <div>
          <p role="alert" aria-label="File listing error" className="mt-0">
            {error}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setError(null);
              setRetry((value) => value + 1);
            }}
          >
            Retry file listing
          </Button>
        </div>
      )}
      {files && (
        <ul className="m-0 flex flex-wrap gap-2 p-0">
          {files.map((entry) => (
            <li key={entry.path} className="min-w-0 max-w-full">
              <Button
                variant="outline"
                className="h-auto max-w-full justify-start gap-2 px-3 py-2 text-left whitespace-normal shadow-none"
                disabled={downloading !== null}
                aria-busy={downloading === entry.path}
                onClick={() => download(entry.path)}
                aria-label={`Download ${entry.path}`}
              >
                <FileText aria-hidden="true" className="shrink-0 text-muted-foreground" />
                <span className="min-w-0 break-all text-xs font-normal">{entry.path}</span>
                {downloading === entry.path ? (
                  <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" />
                ) : (
                  <Download aria-hidden="true" className="size-3.5 text-muted-foreground" />
                )}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {downloadError && (
        <p
          role="alert"
          aria-label="File download error"
          className="mt-3 rounded-md bg-destructive/5 p-3 text-xs"
        >
          {downloadError}
        </p>
      )}
    </section>
  );
}
