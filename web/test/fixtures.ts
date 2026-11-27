import { respond } from "../scripts/mock-api.mjs";

/** The body of the fixture a request is served from (real, or the synthetic edge state of that request). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function fixture<T = any>(path: string, search = ""): T {
  const { status, body } = respond(path, search);
  if (status !== 200) throw new Error(`no fixture for ${path}${search}: ${status}`);
  return body as T;
}

/** "HH:MM ET" of an ISO instant, as the site writes its as-of. */
export function etClock(iso: string): string {
  const t = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
  return `${t} ET`;
}

/** The as-of every page shows: /v1/meta's metrics_as_of. */
export function asOf(): string {
  return etClock(fixture("/v1/meta").data.metrics_as_of ?? fixture("/v1/meta").freshness.metrics_as_of);
}

/** The 24-hour headway-adherence row of a line, as the real API answers it. */
export function headwayRow(line: string, window = "last_24h") {
  return fixture(`/v1/lines/${line}/headways`, `?window=${window}`).data.headway_adherence.rows[0];
}
