import type { components } from "./api.generated";

export type RunDetail = components["schemas"]["RunDetail"];
export type RunSummary = components["schemas"]["RunSummary"];
export type SubmitRunRequest = components["schemas"]["SubmitRunRequest"];

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    cache: "no-store",
    ...init,
    signal: init?.signal
      ? AbortSignal.any([init.signal, AbortSignal.timeout(15_000)])
      : AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      typeof body?.error?.message === "string"
        ? body.error.message
        : `Request failed (${response.status}). Please try again.`,
      response.status,
    );
  }
  return response.json();
}
