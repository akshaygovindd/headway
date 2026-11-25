import type { Envelope } from "./types";

/** Gold is exported about every 15 minutes and the API caches for 300 s: revalidate in step. */
export const REVALIDATE_S = 300;

export type ApiFailure = {
  ok: false;
  kind: "not_available" | "not_found" | "invalid" | "unavailable";
  status: number | null;
  message: string;
};
export type ApiOk<T> = { ok: true; body: Envelope<T> };
export type ApiResult<T> = ApiOk<T> | ApiFailure;

export function apiBase(): string {
  return (process.env.HEADWAY_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

export function failureFor(status: number, body: unknown): ApiFailure {
  const b = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  const reason = typeof b.reason === "string" ? b.reason : null;
  if (status === 404 && b.error === "not_available") {
    return { ok: false, kind: "not_available", status, message: reason ?? "Not published yet" };
  }
  if (status === 404) return { ok: false, kind: "not_found", status, message: "Not found" };
  if (status === 422 || status === 413) {
    return { ok: false, kind: "invalid", status, message: "The request was not valid" };
  }
  return {
    ok: false,
    kind: "unavailable",
    status,
    message: "The data service is unavailable",
  };
}

/** Never throws: network errors, timeouts and bad JSON all come back as an ApiFailure. */
export async function getApi<T>(path: string): Promise<ApiResult<T>> {
  let res: Response;
  try {
    res = await fetch(`${apiBase()}/v1${path}`, {
      next: { revalidate: REVALIDATE_S },
      signal: AbortSignal.timeout(8000),
      headers: { accept: "application/json" },
    });
  } catch {
    return { ok: false, kind: "unavailable", status: null, message: "The data service is unreachable" };
  }
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    if (res.ok) {
      return { ok: false, kind: "unavailable", status: res.status, message: "Unreadable response" };
    }
  }
  if (!res.ok) return failureFor(res.status, body);
  return { ok: true, body: body as Envelope<T> };
}
