// Capture heartbeat and the checks count: both describe Headway's own data collection.

import type { HeartbeatRow, Slo } from "./types";

export type HeartbeatBar = { hour_start: string; completeness: number; outage: boolean; missed: number };

export function heartbeatBars(rows: HeartbeatRow[]): HeartbeatBar[] {
  return rows.map((r) => ({
    hour_start: r.hour_start,
    completeness: r.expected_runs > 0 ? Math.max(0, Math.min(1, 1 - r.missed_runs / r.expected_runs)) : 0,
    outage: r.data_outage,
    missed: r.missed_runs,
  }));
}

/** Share of expected capture runs that completed, 0-100, or null with nothing expected. */
export function capturePct(rows: HeartbeatRow[]): number | null {
  const expected = rows.reduce((n, r) => n + r.expected_runs, 0);
  if (expected <= 0) return null;
  const missed = rows.reduce((n, r) => n + r.missed_runs, 0);
  return (100 * (expected - missed)) / expected;
}

export function outageHours(rows: HeartbeatRow[]): number {
  return rows.filter((r) => r.data_outage).length;
}

export const WARN_BUDGET_USED = 0.75;

/**
 * "N failing, M warnings" from the SLOs over 7 days. Failing: counts toward the SLO and is not
 * met. Warning: met but with at least 75% of the error budget used, or too few days to count.
 */
export function checkCounts(slos: Slo[]): { failing: number; warnings: number } {
  const week = slos.filter((s) => s.window === "last_7d");
  const rows = week.length ? week : slos;
  let failing = 0;
  let warnings = 0;
  for (const s of rows) {
    if (!s.counts_toward_slo) warnings += 1;
    else if (s.met === false) failing += 1;
    else if ((s.error_budget_used ?? 0) >= WARN_BUDGET_USED) warnings += 1;
  }
  return { failing, warnings };
}
