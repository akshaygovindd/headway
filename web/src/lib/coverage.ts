// Coverage states. Full is 70% and over; partial is 50 to under 70; withheld is under 50.
// Pure: the glyph, the dot and the legend all read from here.

import { MIN_COVERAGE_PCT } from "./map";

export const PARTIAL_BELOW_PCT = 70;

export type CoverageState = "full" | "partial" | "withheld" | "unknown";

export function coverageState(coverage: number | null | undefined): CoverageState {
  if (coverage == null || !Number.isFinite(coverage)) return "unknown";
  if (coverage < MIN_COVERAGE_PCT) return "withheld";
  if (coverage < PARTIAL_BELOW_PCT) return "partial";
  return "full";
}

/** How many of the five meter segments are filled (one per 20%, rounded). */
export function meterFill(coverage: number | null | undefined): number {
  if (coverage == null || !Number.isFinite(coverage)) return 0;
  return Math.min(5, Math.max(0, Math.round(coverage / 20)));
}

/** "72% cov." Whole percent: the glyph is a glance, the popover has the precise figure. */
export function covText(coverage: number | null | undefined): string {
  return coverage == null || !Number.isFinite(coverage) ? "cov. unknown" : `${Math.round(coverage)}% cov.`;
}
