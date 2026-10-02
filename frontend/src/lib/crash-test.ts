import type { components } from "./crash-test.generated";

export type CrashSnapshot = components["schemas"]["Snapshot"];
export type CrashSession = components["schemas"]["Session"];
export type Container = components["schemas"]["Container"];

export function scenarioName(name: string) {
  const label = name.replaceAll("-", " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export async function crashRequest<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/crash-api/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal
      ? AbortSignal.any([signal, AbortSignal.timeout(35_000)])
      : AbortSignal.timeout(35_000),
    cache: "no-store",
  });
  const value = await response.json();
  if (!response.ok)
    throw new Error(
      typeof value.detail === "string" ? value.detail : `Controller returned ${response.status}`,
    );
  return value as T;
}

export function serviceName(container: Container) {
  const names: Record<string, string> = {
    control: "Control",
    tools: "Tools",
    worker: "Worker",
    db: "Postgres",
    frontend: "App frontend",
    sandbox: "Sandbox",
  };
  const suffix = container.service === "worker" ? ` ${container.name.split("-").at(-1)}` : "";
  return `${names[container.service] ?? container.service}${suffix}`;
}
