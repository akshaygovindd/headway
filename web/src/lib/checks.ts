// The trust strip's checks figure, from /pipeline/checks: distinct checks whose worst status over
// the window is fail or warn. Pure.

import type { ApiResult } from "./api";
import type { ChecksData } from "./types";

export type ChecksSummary = { failing: number; warnings: number; hours: number; windowText: string };

export function windowText(hours: number): string {
  if (hours > 48 && hours % 24 === 0) return `last ${hours / 24} days`;
  return `last ${hours} h`;
}

/** Null when the endpoint is missing or failed: the strip then says "n/a" rather than a made-up zero. */
export function checksSummary(r: ApiResult<ChecksData>): ChecksSummary | null {
  if (!r.ok) return null;
  const d = r.body.data;
  if (!Number.isFinite(d.n_failing_checks) || !Number.isFinite(d.n_warning_checks)) return null;
  return { failing: d.n_failing_checks, warnings: d.n_warning_checks, hours: d.hours, windowText: windowText(d.hours) };
}

export function checksText(c: ChecksSummary): string {
  return `${c.failing} failing · ${c.warnings} warnings (${c.windowText})`;
}
