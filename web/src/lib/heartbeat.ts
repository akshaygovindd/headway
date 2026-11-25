// Capture heartbeat: Headway's own data collection (the checks count is in checks.ts).

import type { HeartbeatRow } from "./types";

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
