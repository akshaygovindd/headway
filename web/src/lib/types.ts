// Shapes of the Headway metric API (docs/API.md). Values from a row whose coverage is under 50%
// arrive as null with `withheld_reason`.

export type Coverage = {
  rows: number;
  mean_coverage_pct: number | null;
  min_coverage_pct: number | null;
  withheld_rows: number;
  rule: string;
};

export type Freshness = {
  exported_at: string | null;
  metrics_as_of: string | null;
  age_minutes: number | null;
};

export type Provenance = {
  tables: string[];
  definition: string;
  metrics_md: string;
  row_limit: number;
  truncated: boolean;
  served_from: string;
};

export type Envelope<T> = {
  data: T;
  coverage: Coverage;
  freshness: Freshness;
  provenance: Provenance;
};

/** A row that carries a coverage figure and, when its values are null, the reason. */
export type CoveredRow = {
  coverage_pct: number | null;
  withheld_reason: string | null;
  n_observations?: number;
};

export type Section<R> = { table: string; label: string; metrics_md: string; rows: R[] };

export type LineSummary = {
  line: string;
  coverage_pct_last_24h: number | null;
  withheld_last_24h: boolean | null;
  withheld_reason: string | null;
  endpoints: string[];
};

export type PredictedRow = CoveredRow & {
  window: string;
  median_predicted_deviation_s: number | null;
  p90_predicted_deviation_s: number | null;
  median_abs_revision_s: number | null;
  skipped_share: number | null;
};

export type ActualRow = CoveredRow & {
  window: string;
  median_deviation_s: number | null;
  p90_deviation_s: number | null;
  pct_on_time_window: number | null;
};

export type ReliabilityData = {
  line: string;
  window: string;
  predicted_deviation?: Section<PredictedRow>;
  actual_deviation?: Section<ActualRow>;
  unavailable_tables?: string[];
};

export type AccuracyRow = CoveredRow & {
  horizon_bucket: string;
  median_abs_error_s: number | null;
  p90_abs_error_s: number | null;
  mae_s: number | null;
  pct_within_60s: number | null;
  pct_within_120s: number | null;
};

export type AccuracyData = {
  line: string;
  window: string;
  prediction_accuracy?: Section<AccuracyRow>;
  unavailable_tables?: string[];
};

export type HeadwayRow = CoveredRow & {
  pct_within_band: number | null;
  pct_bunched: number | null;
  pct_gap: number | null;
  median_ratio: number | null;
  p90_ratio: number | null;
};

export type HeadwayData = {
  line: string;
  window: string;
  headway_adherence?: Section<HeadwayRow>;
  unavailable_tables?: string[];
};

export type CountdownRow = CoveredRow & {
  station_id: string;
  station_name: string;
  countdown_min: number;
  pct_within_60s: number | null;
  pct_within_120s: number | null;
};

export type CountdownData = {
  line: string;
  window: string;
  countdown_accuracy?: Section<CountdownRow>;
  unavailable_tables?: string[];
};

export type Incident = {
  incident_id: string;
  line: string;
  direction_id: number | null;
  start_ts: string;
  end_ts: string | null;
  n_hours: number;
  is_open: boolean;
  thresholds_crossed: string[] | string | null;
  min_coverage_pct: number | null;
  mean_coverage_pct: number | null;
  coverage_pct: number | null;
  n_outage_hours_inside: number;
  match_status: string | null;
  alert_lag_s: number | null;
  summary_text: string | null;
  template_used: boolean | null;
};

export type IncidentsData = { line: string | null; from: string; to: string; incidents: Incident[] };

export type ForecastRow = {
  direction_id: number;
  horizon_min: number;
  n_trips: number;
  model_forecast_s: number | null;
  model_forecast_s_coverage_pct: number | null;
  persistence: number | null;
  persistence_coverage_pct: number | null;
  daytype_hour: number | null;
  daytype_hour_coverage_pct: number | null;
  rolling_median_60m: number | null;
  rolling_median_60m_coverage_pct: number | null;
  mbta_reference: number | null;
  mbta_reference_coverage_pct: number | null;
  withheld_reasons: Record<string, string>;
};

export type GateRow = {
  line: string;
  n_days: number;
  n_weekend_days: number;
  min_days: number;
  min_weekend_days: number;
  gate_passed: boolean;
};

export type ForecastData = {
  line: string;
  prediction_ts: string | null;
  status: string | null;
  status_reason: string | null;
  model_version: string | null;
  unit: string;
  horizons: ForecastRow[];
  training_gate: GateRow[];
};

export type HealthDay = {
  date: string;
  runs: number;
  vehicle_polls_ok: number;
  vehicle_polls_error: number;
  duplicate_deliveries: number;
  quarantined_records: number;
  trip_update_failures: number;
};

export type HealthData = { days: number; daily: HealthDay[] };

export type Slo = {
  slo_name: string;
  window: string;
  target_pct: number;
  good_events: number;
  total_events: number;
  actual_pct: number | null;
  met: boolean | null;
  error_budget_used: number | null;
  days_covered: number;
  counts_toward_slo: boolean;
};

export type SloData = { slos: Slo[] };

export type MetricDef = {
  name: string;
  label: string;
  higher_is_worse: boolean;
  windows: string[];
  tables: string[];
  metrics_md: string;
};

// --- Release 4: segments, Marey, heartbeat -----------------------------------------------

export type HealthBucket = "even" | "some_bunching" | "bunched_gaps";

export type SegmentRow = {
  direction_id: number;
  from_stop_id: string;
  from_stop_name: string;
  to_stop_id: string;
  to_stop_name: string;
  segment_order: number;
  health: HealthBucket | null;
  pct_within_band: number | null;
  pct_bunched: number | null;
  pct_gap: number | null;
  n_headways: number;
  n_excluded_outage: number;
  disrupted: boolean;
  /** Public MBTA alert header; null unless disrupted (on last_7d it may be null even when disrupted). */
  disruption_header?: string | null;
  coverage_pct: number | null;
  withheld_reason: string | null;
};

export type SegmentsData = { line: string; window: string; direction: number | null; rows: SegmentRow[] };

export type MareyRow = {
  direction_id: number;
  trip_id: string;
  stop_id: string;
  stop_name: string;
  stop_sequence: number;
  arrival_ts: string;
  method: "observed" | "estimated";
  confidence: number | null;
  scheduled_ts: string | null;
};

export type MareyData = { line: string; direction: number | null; hour_start: string | null; rows: MareyRow[] };

export type HeartbeatRow = {
  hour_start: string;
  expected_runs: number;
  missed_runs: number;
  feed_gap_min: number | null;
  data_outage: boolean;
};

export type HeartbeatData = { hours: number; rows: HeartbeatRow[] };

export type MetaData = { api_version: string; lines: string[]; tables_in_export: string[] };

// --- Trip time, spacing by hour, disruptions, checks ---------------------------------------

export type TripTimeStatus = "measured" | "no_trips" | "withheld";

export type TripTimeRow = {
  direction_id: number;
  time_band: "am_peak" | "midday" | "pm_peak" | "evening" | "other";
  n_trips: number;
  n_scheduled_trips: number;
  n_excluded_outage: number;
  n_implausible: number;
  n_disruption: number;
  median_ett_s: number | null;
  p90_ett_s: number | null;
  median_scheduled_s: number | null;
  median_actual_s: number | null;
  coverage_pct: number | null;
  status: TripTimeStatus;
  withheld_reason: string | null;
  no_trips_reason: string | null;
};

export type TripTimeData = {
  line: string;
  window: string;
  unit: "seconds";
  rows: TripTimeRow[];
  overall: (Omit<TripTimeRow, "direction_id" | "time_band" | "median_scheduled_s" | "median_actual_s"> & Partial<TripTimeRow>) | null;
};

export type BudgetRow = {
  direction_id: number;
  time_band: TripTimeRow["time_band"];
  n_trips: number;
  n_scheduled_trips: number;
  n_excluded_outage: number;
  n_implausible: number;
  n_disruption: number;
  median_s: number | null;
  p95_s: number | null;
  buffer_time_index: number | null;
  coverage_pct: number | null;
  status: TripTimeStatus;
  withheld_reason: string | null;
  no_trips_reason: string | null;
};

export type BudgetStation = { id: string; name: string };

/** `stations` is the line's picker list (no from/to); `rows` is one pair's bands. */
export type BudgetData = {
  line: string;
  window: string;
  unit: "seconds";
  min_trips: number;
  from_station: BudgetStation | null;
  to_station: BudgetStation | null;
  stations: BudgetStation[];
  rows: BudgetRow[];
};

export type SpacingStatus = "measured" | "withheld" | "no_headways" | "outage";

export type SpacingHour = {
  service_date: string;
  hour_local: number;
  hour_start: string;
  n_headways: number;
  pct_within_band: number | null;
  pct_bunched: number | null;
  pct_gap: number | null;
  scheduled_headways: number;
  coverage_pct: number | null;
  data_outage: boolean;
  status: SpacingStatus;
  withheld_reason: string | null;
};

export type SpacingData = { line: string; service_date: string; hours: SpacingHour[] };

export type DisruptionRow = {
  alert_id: string;
  effect: string;
  header_text: string | null;
  first_seen: string;
  last_seen: string;
  is_active: boolean;
  n_stations: number;
  from_station_name: string | null;
  to_station_name: string | null;
  segments: { direction_id: number; from_stop_id: string; to_stop_id: string }[];
};

export type DisruptionsData = { line: string; window: string; rows: DisruptionRow[] };

export type QualityCheck = {
  check_name: string;
  n_hours_evaluated: number;
  n_fail_hours: number;
  n_warn_hours: number;
  n_ok_hours: number;
  worst_status: "fail" | "warn" | "ok" | "not_evaluated";
};

export type ChecksData = {
  hours: number;
  n_checks: number;
  n_failing_checks: number;
  n_warning_checks: number;
  checks: QualityCheck[];
};
