"use client";

import { useEffect, useRef, useState } from "react";
import { request, requestJson, type RunFileListing } from "../lib/api";

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
    <section aria-labelledby="files-heading">
      <h2 id="files-heading">Saved files</h2>
      {!listing && !error && <p>Loading files…</p>}
      {error && (
        <>
          <p role="alert" aria-label="File listing error">
            {error}
          </p>
          <button
            onClick={() => {
              setError(null);
              setRetry((value) => value + 1);
            }}
          >
            Retry file listing
          </button>
        </>
      )}
      {listing?.entries.length === 0 && <p>No files saved yet.</p>}
      {listing && (
        <ul className="file-list">
          {listing.entries.map((entry) => (
            <li key={entry.path}>
              {entry.kind === "directory" ? (
                <span>{entry.path}/</span>
              ) : (
                <button
                  className="file-download"
                  disabled={downloading !== null}
                  onClick={() => download(entry.path)}
                  aria-label={`Download ${entry.path}`}
                >
                  {downloading === entry.path ? "Downloading…" : entry.path}
                  {entry.size_bytes != null && <small> · {entry.size_bytes} bytes</small>}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {downloadError && (
        <p role="alert" aria-label="File download error">
          {downloadError}
        </p>
      )}
    </section>
  );
}
