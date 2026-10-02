import type { ReactNode } from "react";

export function InspectionJson({
  value,
  hideEncrypted = false,
}: {
  value: unknown;
  hideEncrypted?: boolean;
}) {
  return (
    <pre className="mt-2 max-h-80 overflow-auto rounded bg-muted p-3">
      {JSON.stringify(
        value,
        (key, item) =>
          hideEncrypted && key === "encrypted_content" && item
            ? "[Encrypted context present]"
            : item,
        2,
      )}
    </pre>
  );
}

export function InspectionDisclosure({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="my-3">
      <summary className="text-xs font-medium">{title}</summary>
      {children}
    </details>
  );
}
