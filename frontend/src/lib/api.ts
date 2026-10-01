import type { components } from "./api.generated";

export type RunSummary = components["schemas"]["RunSummary"];
export type SubmitRunRequest = components["schemas"]["SubmitRunRequest"];

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    cache: "no-store",
    signal: AbortSignal.timeout(15_000),
    ...init,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.error?.message === "string"
        ? body.error.message
        : `Request failed (${response.status}). Please try again.`,
    );
  }
  return response.json();
}
