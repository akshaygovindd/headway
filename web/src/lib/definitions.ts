// What each number is, in one place. The popover, the CSV comment and the methodology links all
// read from here, so a definition is written once. Anchors are METRICS.md headings.

import { REPO_URL } from "./map";

export type MetricDefinition = {
  label: string;
  what: string;
  /** METRICS.md anchor. */
  anchor: string;
  /** Gold table the number comes from (its dbt model is the SQL). */
  table: string;
};

const HEADWAY = "8-headway-adherence-release-2";
const ACTUAL = "7-actual-schedule-deviation-release-2";

export const DEFINITIONS: Record<string, MetricDefinition> = {
  within_band: {
    label: "Evenly spaced",
    what: "Share of gaps between consecutive trains that were 0.5 to 1.5 times the scheduled gap.",
    anchor: HEADWAY,
    table: "agg_headway_window",
  },
  bunched: {
    label: "Bunched",
    what: "Share of gaps under half the scheduled gap: trains running nose to tail.",
    anchor: HEADWAY,
    table: "agg_headway_window",
  },
  gap: {
    label: "Gaps",
    what: "Share of gaps over 1.5 times the scheduled gap: a long wait.",
    anchor: HEADWAY,
    table: "agg_headway_window",
  },
  on_time: {
    label: "On time",
    what: "Share of arrivals within the on-time window around the timetable.",
    anchor: ACTUAL,
    table: "agg_actual_deviation_window",
  },
  deviation: {
    label: "Deviation from the timetable",
    what: "How far observed arrivals were from the scheduled time (positive is late).",
    anchor: ACTUAL,
    table: "agg_actual_deviation_window",
  },
  predicted: {
    label: "Predicted deviation",
    what: "How late or early the MBTA's own predictions said trains would be.",
    anchor: "1-predicted-schedule-deviation",
    table: "agg_line_reliability_window",
  },
  accuracy: {
    label: "Accuracy of MBTA predictions",
    what: "Difference between what the MBTA predicted and when the train arrived, by minutes ahead.",
    anchor: "release-2-metrics-shared-rules",
    table: "agg_accuracy_window",
  },
  countdown: {
    label: "Countdown accuracy",
    what: "How often the countdown shown at a station was right to within the stated margin.",
    anchor: "10-countdown-accuracy-release-2",
    table: "agg_countdown_accuracy_window",
  },
  segment: {
    label: "Spacing between two stations",
    what: "Headway adherence for one stretch between neighbouring stops.",
    anchor: "release-4-site-metrics",
    table: "agg_headway_segment_window",
  },
  capture: {
    label: "Captures",
    what: "Share of the one-minute capture runs that completed in the last 24 hours.",
    anchor: "release-4-site-metrics",
    table: "agg_capture_heartbeat_hourly",
  },
  measured: {
    label: "Arrivals measured",
    what: "Average share of scheduled trips Headway could observe, across lines.",
    anchor: "3-coverage",
    table: "agg_line_reliability_window",
  },
  checks: {
    label: "Pipeline checks",
    what: "Data-quality checks of Headway's own pipeline over the window, counted as distinct checks. Failing: at least one evaluated hour failed. Warning: no failed hour but at least one warned. Hours with no service or no data are not evaluated.",
    anchor: "quality-checks-on-the-trust-strip",
    table: "agg_quality_checks_hourly",
  },
  trip_time: {
    label: "Trip time against the timetable",
    what: "Actual running time minus the planned running time of the same trips, per direction and time band (positive is slower). An adapted approximation, not an official measure.",
    anchor: "trip-time-per-direction-and-band-agg_ett_band_window",
    table: "agg_ett_band_window",
  },
  spacing_hour: {
    label: "Spacing by hour",
    what: "Share of gaps between trains within the expected band in one service hour, both directions combined.",
    anchor: HEADWAY,
    table: "agg_headway_adherence_hourly",
  },
  generic: {
    label: "Measurement",
    what: "A figure computed by Headway from public MBTA feeds.",
    anchor: "shared-rules",
    table: "agg_headway_window",
  },
};

export function definitionFor(key: string | undefined): MetricDefinition {
  return DEFINITIONS[key ?? "generic"] ?? (DEFINITIONS.generic as MetricDefinition);
}

export function sqlUrl(table: string): string {
  return `${REPO_URL}/blob/main/dbt/headway/models/marts/${table}.sql`;
}

/** What the popover's "Left out" line says: the count if one is published, else that none is. */
export function leftOutText(n: number | null | undefined, what = "observations"): string {
  return n == null ? "No left-out count is published for this figure" : `${n} ${what} in hours when our own capture had a gap (not evaluated)`;
}

/** "capture_completeness" -> "Capture Completeness". */
export function sloTitle(name: string): string {
  return name
    .split("_")
    .map((w) => (w ? w[0]!.toUpperCase() + w.slice(1) : w))
    .join(" ");
}

export function docUrl(path: string): string {
  return `${REPO_URL}/blob/main/${path}`;
}
