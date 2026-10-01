"use client";

import { useEffect, useRef, useState } from "react";
import { Download, FileText, Folder, LoaderCircle } from "lucide-react";
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

  return (
    <section aria-labelledby="files-heading" className="min-w-0 overflow-hidden p-0">
      <h2 id="files-heading" className="border-b px-5 py-4">
        Saved files
      </h2>
      {!listing && !error && <p className="m-0 p-5 text-muted-foreground">Loading files…</p>}
      {error && (
        <div className="p-5">
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
      {listing?.entries.length === 0 && (
        <p className="m-0 p-5 text-muted-foreground">No files saved yet.</p>
      )}
      {listing && (
        <ul className="m-0 divide-y p-0">
          {listing.entries.map((entry) => (
            <li key={entry.path}>
              {entry.kind === "directory" ? (
                <span className="flex items-center gap-2 px-5 py-2 text-xs text-muted-foreground">
                  <Folder aria-hidden="true" className="size-4 shrink-0" />
                  <span className="break-all">{entry.path}/</span>
                </span>
              ) : (
                <Button
                  variant="ghost"
                  className="h-auto w-full justify-start gap-3 rounded-none px-5 py-3 text-left whitespace-normal focus-visible:ring-inset"
                  disabled={downloading !== null}
                  aria-busy={downloading === entry.path}
                  onClick={() => download(entry.path)}
                  aria-label={`Download ${entry.path}`}
                >
                  <FileText aria-hidden="true" className="shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="block break-all text-xs">{entry.path}</span>
                    {entry.size_bytes != null && (
                      <span className="mt-1 block text-xs font-normal text-muted-foreground">
                        {entry.size_bytes.toLocaleString()} bytes
                      </span>
                    )}
                  </span>
                  {downloading === entry.path ? (
                    <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" />
                  ) : (
                    <Download aria-hidden="true" className="size-3.5 text-muted-foreground" />
                  )}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {downloadError && (
        <p
          role="alert"
          aria-label="File download error"
          className="m-0 border-t bg-destructive/5 p-5"
        >
          {downloadError}
        </p>
      )}
    </section>
  );
}
