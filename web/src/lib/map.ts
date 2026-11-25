// Pure mapping and formatting. The rule behind every number on the site: a value renders only
// with its coverage beside it, and a value from a row under MIN_COVERAGE_PCT never renders.

export const MIN_COVERAGE_PCT = 50;

export const REPO_URL = process.env.NEXT_PUBLIC_REPO_URL ?? "https://github.com/akshaygovindd/headway";

export const HEAVY_RAIL = ["Red", "Orange", "Blue"] as const;
export const LINES = [
  "Red",
  "Orange",
  "Blue",
  "Green-B",
  "Green-C",
  "Green-D",
  "Green-E",
  "Mattapan",
] as const;
export type LineName = (typeof LINES)[number];

export function isLine(value: string): value is LineName {
  return (LINES as readonly string[]).includes(value);
}

export function isHeavyRail(line: string): boolean {
  return (HEAVY_RAIL as readonly string[]).includes(line);
}

export type Cell = {
  value: number | null | undefined;
  coverage_pct: number | null | undefined;
  withheld_reason?: string | null;
};

export type Shown =
  | { kind: "value"; value: number; coverage: number }
  /** Coverage is known and under the minimum (or the API gave a reason): withheld on purpose. */
  | { kind: "withheld"; coverage: number | null; reason: string | null }
  /** No value and no coverage figure to judge it by: missing, not low. Never called "withheld". */
  | { kind: "unavailable"; coverage: number | null; reason: null };

export const UNAVAILABLE_TEXT = "Data unavailable right now";

/**
 * Decide whether a value may be shown. Defensive: checks the coverage even if the API did not.
 * Only coverage under the minimum (or an API reason) is "withheld"; a missing value or a value
 * with no coverage figure is "unavailable".
 */
export function shown(cell: Cell): Shown {
  const cov = cell.coverage_pct ?? null;
  const hasValue = cell.value != null && Number.isFinite(cell.value);
  if (cell.withheld_reason || (cov != null && cov < MIN_COVERAGE_PCT)) {
    return { kind: "withheld", coverage: cov, reason: cell.withheld_reason ?? null };
  }
  if (hasValue && cov != null) return { kind: "value", value: cell.value as number, coverage: cov };
  return { kind: "unavailable", coverage: cov, reason: null };
}

export function round(value: number, digits: number): number {
  const f = 10 ** digits;
  const r = Math.round((value + Number.EPSILON) * f) / f;
  return Object.is(r, -0) ? 0 : r;
}

export function formatNumber(value: number, digits = 0): string {
  return round(value, digits).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export const TIMING_FLOOR_S = 20;

export type Unit = "s" | "min" | "s20" | "pct" | "pct0" | "share" | "ratio" | "count" | "h";

export function formatValue(value: number, unit: Unit): string {
  switch (unit) {
    case "s":
      return `${formatNumber(value, 0)} s`;
    case "min":
      return `${formatNumber(value / 60, 1)} min`;
    case "s20":
      // Timing is about +/-20 s, so anything under that floor reads "~20 s", not a false precision.
      return value <= TIMING_FLOOR_S ? `~${TIMING_FLOOR_S} s` : `${formatNumber(value, 0)} s`;
    case "pct0":
      return `${formatNumber(value, 0)}%`;
    case "pct":
      return `${formatNumber(value, 1)}%`;
    case "share":
      return `${formatNumber(value * 100, 1)}%`;
    case "ratio":
      return formatNumber(value, 2);
    case "count":
      return formatNumber(value, 0);
    case "h":
      return `${formatNumber(value, 0)} h`;
  }
}

export function coverageText(coverage: number | null | undefined): string {
  return coverage == null ? "coverage unknown" : `coverage ${formatNumber(coverage, 1)}%`;
}

export function withheldText(coverage: number | null | undefined): string {
  return `Not enough data \u00b7 ${coverage == null ? "cov. unknown" : `${Math.round(coverage)}% cov.`}`;
}

/** "updated N min ago" from the API's age_minutes. */
export function freshnessText(ageMinutes: number | null | undefined): string {
  if (ageMinutes == null || !Number.isFinite(ageMinutes) || ageMinutes < 0) {
    return "update time unknown";
  }
  const m = Math.floor(ageMinutes);
  if (m < 1) return "updated just now";
  if (m < 60) return `updated ${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `updated ${h} h ago`;
  return `updated ${Math.floor(h / 24)} days ago`;
}

/** Minutes between the export time and `nowMs`, computed when the page is viewed. Null if unparsable. */
export function ageAt(exportedAt: string | null | undefined, nowMs: number): number | null {
  const t = exportedAt ? Date.parse(exportedAt) : NaN;
  return Number.isFinite(t) ? Math.max(0, (nowMs - t) / 60000) : null;
}

/** Gold is refreshed about every 15 minutes; older than this the page says it is stale. */
export const STALE_AFTER_MIN = 60;

export function isStale(ageMinutes: number | null | undefined): boolean {
  return ageMinutes == null || ageMinutes > STALE_AFTER_MIN;
}

export type Trend =
  | { kind: "withheld" }
  | { kind: "unavailable" }
  | { kind: "flat" | "up" | "down"; delta: number };

/** Last 24 hours against the 7-day figure. Withheld unless both sides may be shown. */
export function trend(day: Cell, week: Cell, flatBelow: number): Trend {
  const d = shown(day);
  const w = shown(week);
  if (d.kind === "unavailable" || w.kind === "unavailable") return { kind: "unavailable" };
  if (d.kind !== "value" || w.kind !== "value") return { kind: "withheld" };
  const delta = round(d.value - w.value, 2);
  if (Math.abs(delta) < flatBelow) return { kind: "flat", delta };
  return { kind: delta > 0 ? "up" : "down", delta };
}

export const GATE_TEXT = "comparison unlocks after 21 service days incl. 3 weekends";

export function gateProgress(g: {
  n_days: number;
  n_weekend_days: number;
  min_days: number;
  min_weekend_days: number;
  gate_passed: boolean;
}): string {
  if (g.gate_passed) return "gate passed";
  return `${g.n_days} of ${g.min_days} service days, ${g.n_weekend_days} of ${g.min_weekend_days} weekend days`;
}

export function methodologyUrl(metricsMd: string): string {
  const path = metricsMd.startsWith("docs/") ? metricsMd : `docs/METRICS.md#${metricsMd}`;
  return `${REPO_URL}/blob/main/${path}`;
}
