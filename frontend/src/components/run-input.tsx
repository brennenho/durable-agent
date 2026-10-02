import type { RunDetail } from "../lib/api";

export function RunInput({ run, label = "Your message" }: { run: RunDetail; label?: string }) {
  const hasSdkInput = Boolean(run.agent) && run.input !== undefined;
  const input = hasSdkInput ? run.input : run.prompt;
  if (!hasSdkInput && !input) return null;
  const plainText = typeof input === "string" && input.trim().length > 0;
  return (
    <div
      aria-label={label}
      className="ml-auto w-fit max-w-[90%] rounded-xl bg-muted px-4 py-3 whitespace-pre-wrap wrap-anywhere"
    >
      {plainText ? (
        input
      ) : (
        <pre className="m-0 max-h-80 overflow-auto font-mono text-xs leading-relaxed whitespace-pre-wrap wrap-anywhere">
          {JSON.stringify(input, null, 2)}
        </pre>
      )}
    </div>
  );
}
