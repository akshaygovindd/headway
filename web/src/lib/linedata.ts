// Trip-time and spacing-by-hour rows turned into what the line page draws. Pure.

import { healthBucket, STATE_LABEL, type Health } from "./health";
import { hourLabel } from "./time";
import type { BudgetRow, SpacingHour, TripTimeRow } from "./types";

const BAND_LABEL: Record<string, string> = {
  am_peak: "Morning peak (06:30\u201309:00)",
  midday: "Midday (09:00\u201315:30)",
  pm_peak: "Afternoon peak (15:30\u201318:30)",
  evening: "Evening (18:30\u201324:00)",
  other: "Other hours",
};
export const bandLabel = (b: string): string => BAND_LABEL[b] ?? b;

export type TripCell =
  | { kind: "no_trips"; reason: string | null }
  | { kind: "withheld"; coverage: number | null; reason: string | null }
  | { kind: "measured" };

export const WINDOW_LABEL: Record<string, string> = { last_7d: "the last 7 days", last_24h: "the last 24 hours" };

/** The time band (same bands as the data) of a moment, in Eastern time. */
export function currentBand(now: Date): BudgetRow["time_band"] {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "numeric", hourCycle: "h23" }).formatToParts(now);
  const at = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  const m = at("hour") * 60 + at("minute");
  if (m >= 390 && m < 540) return "am_peak";
  if (m >= 540 && m < 930) return "midday";
  if (m >= 930 && m < 1110) return "pm_peak";
  if (m >= 1110) return "evening";
  return "other";
}

/**
 * The band the headline sentence is about: the current band when it has a measured budget, else the
 * measured band with the most trips; null when no band is measured (everything withheld or empty).
 */
export function headlineBand(rows: BudgetRow[], now: Date): BudgetRow | null {
  const ok = rows.filter((r) => r.status === "measured" && r.median_s != null && r.p95_s != null);
  const cur = currentBand(now);
  return ok.find((r) => r.time_band === cur) ?? [...ok].sort((a, b) => b.n_trips - a.n_trips)[0] ?? null;
}

/** `no_trips` is the only state that may say "No trips measured yet"; withheld is its own state. */
export function tripCell(r: TripTimeRow): TripCell {
  if (r.status === "no_trips") return { kind: "no_trips", reason: r.no_trips_reason };
  if (r.status === "withheld" || r.median_ett_s == null) return { kind: "withheld", coverage: r.coverage_pct, reason: r.withheld_reason };
  return { kind: "measured" };
}

export type HourState = "outage" | "withheld" | "no_headways" | Health;

export function hourState(h: SpacingHour): HourState {
  if (h.status === "outage" || h.data_outage) return "outage";
  if (h.status === "no_headways") return "no_headways";
  if (h.status === "withheld") return "withheld";
  return healthBucket(h.pct_within_band) ?? "withheld";
}

export function hourText(h: SpacingHour): string {
  const st = hourState(h);
  const t = hourLabel(h.hour_local);
  if (st === "outage") return `${t}: our data gap, capture was down, left out of every number`;
  if (st === "no_headways") return `${t}: no valid headways measured`;
  if (st === "withheld") return `${t}: not enough data${h.coverage_pct != null ? `, ${Math.round(h.coverage_pct)}% cov.` : ""}`;
  return `${t}: ${STATE_LABEL[st]}, ${Math.round(h.pct_within_band ?? 0)}% within the band, ${Math.round(h.coverage_pct ?? 0)}% cov.`;
}

/** Order 05:00 to 01:00 whatever the API sent. */
export function orderHours(hours: SpacingHour[]): SpacingHour[] {
  const key = (h: number) => (h < 5 ? h + 24 : h);
  return [...hours].sort((a, b) => key(a.hour_local) - key(b.hour_local));
}

/**
 * Countdown rows that belong to this line: the row must name this line. The API already filters by
 * line; this is a guard so a station of another line is never shown under this one. A row with no
 * line falls back to the line's own stops (from /segments). Rows are keyed by station and segments
 * by platform stop, so stop ids cannot be compared with `station_id` for a row that has a line.
 */
export function countdownRowsFor<R extends { station_id: string; line?: string }>(rows: R[], line: string, stopIds: Set<string>): R[] {
  return rows.filter((r) => (r.line != null ? r.line === line : stopIds.size === 0 || stopIds.has(r.station_id)));
}

/** Horizon buckets ("0-5", "5-10", "10-20", "20-30") in numeric order, whatever order they arrived in. */
export function sortBuckets<R extends { horizon_bucket: string }>(rows: R[]): R[] {
  const first = (b: string) => {
    const n = Number.parseFloat(b);
    return Number.isFinite(n) ? n : Number.POSITIVE_INFINITY;
  };
  return [...rows].sort((a, b) => first(a.horizon_bucket) - first(b.horizon_bucket) || a.horizon_bucket.localeCompare(b.horizon_bucket));
}
