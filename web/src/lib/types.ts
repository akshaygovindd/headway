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
