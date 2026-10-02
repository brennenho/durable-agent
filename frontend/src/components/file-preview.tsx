"use client";

import { useEffect, useState, type RefObject } from "react";
import { Dialog } from "radix-ui";
import { Download, X } from "lucide-react";
import { request, type RunFileListing } from "../lib/api";
import { MarkdownAnswer } from "./markdown-answer";
import { Button } from "./ui/button";

const previewLimit = 256 * 1024;
const textExtension =
  /\.(md|markdown|txt|py|json|csv|toml|ya?ml|js|jsx|ts|tsx|css|html|sh|sql|log)$/i;

async function readPreview(response: Response): Promise<string | null> {
  const reader = response.body?.getReader();
  if (!reader) return "";
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > previewLimit) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    for (const character of text) {
      if (character.charCodeAt(0) < 32 && !"\n\r\t".includes(character)) return null;
    }
    return text;
  } catch {
    return null;
  }
}

function PreviewContent({ url, markdown }: { url: string; markdown: boolean }) {
  const [content, setContent] = useState<string | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    request(url, { signal: controller.signal })
      .then(readPreview)
      .then((text) => {
        if (!controller.signal.aborted) setContent(text);
      })
      .catch((cause) => {
        if (!controller.signal.aborted)
          setError(cause instanceof Error ? cause.message : "Could not preview this file.");
      });
    return () => controller.abort();
  }, [url]);
  if (error) return <p role="alert">{error} Download the file or close and reopen to retry.</p>;
  if (content === undefined) return <p role="status">Loading preview…</p>;
  if (content === null)
    return <p>This file cannot be previewed. Download it to view its contents.</p>;
  if (!content) return <p className="text-muted-foreground">This file is empty.</p>;
  return markdown ? (
    <MarkdownAnswer text={content} />
  ) : (
    <pre className="whitespace-pre-wrap break-all">{content}</pre>
  );
}

export function FilePreview({
  runId,
  versionId,
  entry,
  onClose,
  onDownload,
  downloading,
  downloadError,
  trigger,
}: {
  runId: string;
  versionId: string;
  entry: RunFileListing["entries"][number];
  onClose: () => void;
  onDownload: () => void;
  downloading: boolean;
  downloadError: string | null;
  trigger: RefObject<HTMLButtonElement | null>;
}) {
  const name = entry.path.split("/").pop() || entry.path;
  const supported = !name.includes(".") || textExtension.test(name);
  const tooLarge = (entry.size_bytes ?? 0) > previewLimit;
  const query = new URLSearchParams({ version_id: versionId, path: entry.path });
  const url = `/api/runs/${encodeURIComponent(runId)}/file?${query}`;
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-foreground/20" />
        <Dialog.Content
          aria-describedby={undefined}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            trigger.current?.focus();
          }}
          className="fixed top-1/2 left-1/2 z-50 flex max-h-[85dvh] w-[calc(100%-2rem)] max-w-3xl -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border bg-background shadow-lg"
        >
          <div className="flex items-center gap-3 border-b p-4">
            <Dialog.Title
              className="min-w-0 flex-1 truncate text-sm font-medium"
              title={entry.path}
            >
              {entry.path}
            </Dialog.Title>
            <Button variant="outline" size="sm" disabled={downloading} onClick={onDownload}>
              <Download aria-hidden="true" />
              {downloading ? "Downloading…" : "Download"}
            </Button>
            <Dialog.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Close preview">
                <X />
              </Button>
            </Dialog.Close>
          </div>
          <div className="min-h-24 overflow-auto p-5">
            {downloadError && (
              <p role="alert" aria-label="Preview download error">
                {downloadError}
              </p>
            )}
            {!supported || tooLarge ? (
              <p>
                {tooLarge
                  ? "This file exceeds the 256 KiB preview limit."
                  : "Preview is unavailable for this file type."}{" "}
                Download it to view its contents.
              </p>
            ) : (
              <PreviewContent key={url} url={url} markdown={/\.(md|markdown)$/i.test(name)} />
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
