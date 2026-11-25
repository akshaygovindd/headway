// Spacing health of a stretch of line: the share of headways within the band decides the bucket.
// The thresholds match the dbt model (agg_headway_segment_window) and are fixed, not tuned.

export type Health = "even" | "some_bunching" | "bunched_gaps";
export type SegmentState = Health | "withheld";

export const EVEN_AT = 80;
export const SOME_BUNCHING_AT = 60;

export function healthBucket(pctWithinBand: number | null | undefined): Health | null {
  if (pctWithinBand == null || !Number.isFinite(pctWithinBand)) return null;
  if (pctWithinBand >= EVEN_AT) return "even";
  if (pctWithinBand >= SOME_BUNCHING_AT) return "some_bunching";
  return "bunched_gaps";
}

/** The state to draw: the API's bucket, else one computed from the share; withheld wins. */
export function segmentState(row: {
  health: Health | null;
  pct_within_band: number | null;
  withheld_reason: string | null;
}): SegmentState {
  if (row.withheld_reason) return "withheld";
  return row.health ?? healthBucket(row.pct_within_band) ?? "withheld";
}

export const HEALTH_RAMP: Record<Health, string> = {
  even: "#3346B8",
  some_bunching: "#8F6BD6",
  bunched_gaps: "#C93A8B",
};

/** Text legend shown beside every use of the ramp: colour never carries the meaning alone. */
export const HEALTH_LEGEND: { state: Health; label: string }[] = [
  { state: "even", label: `Evenly spaced (${EVEN_AT}% or more within the band)` },
  { state: "some_bunching", label: `Some bunching (${SOME_BUNCHING_AT} to ${EVEN_AT - 1}%)` },
  { state: "bunched_gaps", label: `Bunched or gaps (under ${SOME_BUNCHING_AT}%)` },
];

export const STATE_LABEL: Record<SegmentState, string> = {
  even: "evenly spaced",
  some_bunching: "some bunching",
  bunched_gaps: "bunched or gaps",
  withheld: "not enough data",
};
