import type { ReactNode } from "react";

export function ToolArguments({ value }: { value: Record<string, unknown> }) {
  if (Object.keys(value).length === 0)
    return <p className="text-xs text-muted-foreground">No arguments.</p>;
  return (
    <dl className="my-3 space-y-3">
      {Object.entries(value).map(([name, item]) => (
        <div key={name}>
          <dt className="mb-1 text-xs text-muted-foreground">{name}</dt>
          <dd className="m-0">
            {typeof item === "string" ? (
              <pre className="max-h-80 overflow-auto rounded bg-muted/60 px-3 py-2">
                {item || "(empty)"}
              </pre>
            ) : (
              <InspectionJson value={item} />
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

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
