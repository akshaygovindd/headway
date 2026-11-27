"""Response shapes of the public API, for the OpenAPI schema (and so the site's generated types).

Documentation only: routes return their dicts unchanged (``responses=`` names a model, it does not
filter or validate the answer). ``tests/unit/test_api_schema.py`` validates every API answer of the
real snapshot against these models, so a field the API stops sending, or sends under another type,
fails a test. Models allow extra fields: a new field is not a break, a changed one is.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TimeBand = Literal["am_peak", "midday", "pm_peak", "evening", "other"]
Severity = Literal["minor", "moderate", "severe"]
TripTimeStatus = Literal["measured", "no_trips", "withheld"]
SlowZoneStatus = Literal["matched", "observed_only", "listed_only", "listed_unmapped"]


class Row(BaseModel):
    model_config = ConfigDict(extra="allow")


class Coverage(Row):
    rows: int
    mean_coverage_pct: float | None
    min_coverage_pct: float | None
    withheld_rows: int
    rule: str


class Freshness(Row):
    exported_at: str | None
    metrics_as_of: str | None
    age_minutes: float | None


class Provenance(Row):
    tables: list[str]
    definition: str
    metrics_md: str
    row_limit: int
    truncated: bool
    served_from: str
    sql: list[str] | None = None


class Envelope[T](Row):
    data: T
    coverage: Coverage
    freshness: Freshness
    provenance: Provenance


class CoveredRow(Row):
    coverage_pct: float | None
    withheld_reason: str | None
    n_observations: int = None  # type: ignore[assignment]  # omitted when the row has no count, never null


class Section[R](Row):
    table: str
    label: str
    metrics_md: str
    rows: list[R]


class LineSummary(Row):
    line: str
    coverage_pct_last_24h: float | None
    withheld_last_24h: bool | None
    withheld_reason: str | None
    endpoints: list[str]


class PredictedRow(CoveredRow):
    window: str
    median_predicted_deviation_s: float | None
    p90_predicted_deviation_s: float | None
    median_abs_revision_s: float | None
    skipped_share: float | None


class ActualRow(CoveredRow):
    window: str
    median_deviation_s: float | None
    p90_deviation_s: float | None
    pct_on_time_window: float | None


class ReliabilityData(Row):
    line: str
    window: str
    predicted_deviation: Section[PredictedRow] = None  # type: ignore[assignment]  # omitted, never null, when its table is unavailable
    actual_deviation: Section[ActualRow] = None  # type: ignore[assignment]  # omitted, never null, when its table is unavailable
    unavailable_tables: list[str] = None  # type: ignore[assignment]  # omitted when none


class AccuracyRow(CoveredRow):
    horizon_bucket: str
    median_abs_error_s: float | None
    p90_abs_error_s: float | None
    mae_s: float | None
    pct_within_60s: float | None
    pct_within_120s: float | None


class AccuracyData(Row):
    line: str
    window: str
    prediction_accuracy: Section[AccuracyRow] = None  # type: ignore[assignment]  # omitted, never null, when its table is unavailable
    unavailable_tables: list[str] = None  # type: ignore[assignment]  # omitted when none


class HeadwayRow(CoveredRow):
    pct_within_band: float | None
    pct_bunched: float | None
    pct_gap: float | None
    median_ratio: float | None
    p90_ratio: float | None


class HeadwayData(Row):
    line: str
    window: str
    headway_adherence: Section[HeadwayRow] = None  # type: ignore[assignment]  # omitted, never null, when its table is unavailable
    unavailable_tables: list[str] = None  # type: ignore[assignment]  # omitted when none


class CountdownRow(CoveredRow):
    station_id: str
    station_name: str
    countdown_min: int
    pct_within_60s: float | None
    pct_within_120s: float | None


class CountdownData(Row):
    line: str
    window: str
    countdown_accuracy: Section[CountdownRow] = None  # type: ignore[assignment]  # omitted, never null, when its table is unavailable
    unavailable_tables: list[str] = None  # type: ignore[assignment]  # omitted when none


class Incident(Row):
    incident_id: str
    line: str
    direction_id: int | None
    start_ts: str
    end_ts: str | None
    n_hours: int
    is_open: bool
    thresholds_crossed: list[str] | str | None
    min_coverage_pct: float | None
    mean_coverage_pct: float | None
    coverage_pct: float | None
    n_outage_hours_inside: int
    match_status: str | None
    alert_lag_s: float | None
    summary_text: str | None
    template_used: bool | None


class IncidentsData(Row):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    line: str | None
    from_: str = Field(alias="from")
    to: str
    incidents: list[Incident]


class ForecastRow(Row):
    direction_id: int
    horizon_min: int
    n_trips: int
    model_forecast_s: float | None
    model_forecast_s_coverage_pct: float | None
    persistence: float | None
    persistence_coverage_pct: float | None
    daytype_hour: float | None
    daytype_hour_coverage_pct: float | None
    rolling_median_60m: float | None
    rolling_median_60m_coverage_pct: float | None
    mbta_reference: float | None
    mbta_reference_coverage_pct: float | None
    withheld_reasons: dict[str, str]


class GateRow(Row):
    line: str
    n_days: int
    n_weekend_days: int
    min_days: int
    min_weekend_days: int
    gate_passed: bool


class ForecastData(Row):
    line: str
    prediction_ts: str | None
    status: str | None
    status_reason: str | None
    model_version: int | None
    unit: str
    horizons: list[ForecastRow]
    training_gate: list[GateRow]


class HealthDay(Row):
    date: str
    runs: int
    vehicle_polls_ok: int
    vehicle_polls_error: int
    duplicate_deliveries: int
    quarantined_records: int
    trip_update_failures: int


class HealthData(Row):
    days: int
    daily: list[HealthDay]


class Slo(Row):
    slo_name: str
    window: str
    target_pct: float
    good_events: float
    total_events: float
    actual_pct: float | None
    met: bool | None
    error_budget_used: float | None
    days_covered: int
    counts_toward_slo: bool


class SloData(Row):
    slos: list[Slo]


class MetricDef(Row):
    name: str
    label: str
    higher_is_worse: bool
    windows: list[str]
    tables: list[str]
    metrics_md: str


class SegmentRow(Row):
    direction_id: int
    from_stop_id: str
    from_stop_name: str
    to_stop_id: str
    to_stop_name: str
    segment_order: int
    health: Literal["even", "some_bunching", "bunched_gaps"] | None
    pct_within_band: float | None
    pct_bunched: float | None
    pct_gap: float | None
    n_headways: int
    n_excluded_outage: int
    disrupted: bool
    disruption_header: str | None = None
    coverage_pct: float | None
    withheld_reason: str | None


class SegmentsData(Row):
    line: str
    window: str
    direction: int | None
    rows: list[SegmentRow]


class MareyRow(Row):
    direction_id: int
    trip_id: str
    stop_id: str
    stop_name: str
    stop_sequence: int
    arrival_ts: str
    method: Literal["observed", "estimated"]
    confidence: Literal["high", "medium", "low"] | None
    scheduled_ts: str | None


class MareyData(Row):
    line: str
    direction: int | None
    hour_start: str | None
    rows: list[MareyRow]


class HeartbeatRow(Row):
    hour_start: str
    expected_runs: float
    missed_runs: float
    feed_gap_min: float | None
    data_outage: bool


class HeartbeatData(Row):
    hours: int
    rows: list[HeartbeatRow]


class MetaData(Row):
    api_version: str
    lines: list[str]
    tables_in_export: list[str]


class TripTimeRow(Row):
    direction_id: int
    time_band: TimeBand
    n_trips: int
    n_scheduled_trips: int
    n_excluded_outage: int
    n_implausible: int
    n_disruption: int
    median_ett_s: float | None
    p90_ett_s: float | None
    median_scheduled_s: float | None
    median_actual_s: float | None
    coverage_pct: float | None
    status: TripTimeStatus
    withheld_reason: str | None
    no_trips_reason: str | None


class TripTimeOverall(Row):
    n_trips: int
    n_scheduled_trips: int
    n_excluded_outage: int
    n_implausible: int
    n_disruption: int
    median_ett_s: float | None
    p90_ett_s: float | None
    coverage_pct: float | None
    status: TripTimeStatus
    withheld_reason: str | None
    no_trips_reason: str | None


class TripTimeData(Row):
    line: str
    window: str
    unit: Literal["seconds"]
    rows: list[TripTimeRow]
    overall: TripTimeOverall | None


class BudgetRow(Row):
    direction_id: int
    time_band: TimeBand
    n_trips: int
    n_scheduled_trips: int
    n_excluded_outage: int
    n_implausible: int
    n_disruption: int
    median_s: float | None
    p95_s: float | None
    buffer_time_index: float | None
    coverage_pct: float | None
    status: TripTimeStatus
    withheld_reason: str | None
    no_trips_reason: str | None


class BudgetStation(Row):
    id: str
    name: str


class BudgetData(Row):
    line: str
    window: str
    unit: Literal["seconds"]
    min_trips: int
    from_station: BudgetStation | None
    to_station: BudgetStation | None
    stations: list[BudgetStation] | None = None  # the line's picker list; a pair's answer has none
    rows: list[BudgetRow]


class SlowSegmentRow(Row):
    line: str
    direction_id: int
    from_stop_id: str
    from_stop_name: str | None
    to_stop_id: str
    to_stop_name: str | None
    segment_order: int | None
    time_band: TimeBand
    n_trips: int
    n_baseline_trips: int
    n_disruption: int
    normal_s: float | None
    observed_s: float | None
    excess_s: float | None
    excess_pct: float | None
    share_slow_pct: float | None
    severity: Severity | None
    first_seen: str | None
    last_seen: str | None
    coverage_pct: float | None
    withheld_reason: str | None


class SlowComparisonRow(Row):
    status: SlowZoneStatus
    observed_status: Literal["flagged", "not_flagged", "not_enough_data"]
    line: str
    direction_id: int | None
    from_stop_id: str | None
    from_stop_name: str | None
    to_stop_id: str | None
    to_stop_name: str | None
    segment_order: int | None
    severity: Severity | None
    worst_excess_s: float | None
    n_trips: int | None
    flagged_bands: list[str]
    first_seen: str | None
    last_seen: str | None
    coverage_pct: float | None
    restriction_ids: list[str]
    official_speed_limit_mph: int | None
    official_date_reported: str | None
    location_text: str | None


class OfficialList(Row):
    status: Literal["captured", "not_captured"]
    is_snapshot: Literal[True]
    snapshot_date: str | None
    source_url: str | None
    note: str


class SlowZoneCounts(Row):
    matched: int
    observed_only: int
    listed_only: int
    listed_unmapped: int


class SlowZonesData(Row):
    line: str | None
    window: str
    signal: str
    official: OfficialList
    counts: SlowZoneCounts
    segments: list[SlowSegmentRow]
    comparison: list[SlowComparisonRow]


class AccessibilityStation(Row):
    station_id: str
    station_name: str
    lines: list[str]
    n_outages: int | None
    n_elevator_outages: int | None
    n_escalator_outages: int | None
    outage_hours: float | None
    currently_out: bool
    n_currently_out: int
    longest_open_outage_h: float | None
    current_outage_text: list[str]
    coverage_pct: float | None
    withheld_reason: str | None


class AccessibilityData(Row):
    line: str | None
    window: str
    station: str | None
    signal: str
    n_stations_in_network: int
    n_stations_out_now: int
    stations: list[AccessibilityStation]


class SpacingHour(Row):
    service_date: str
    hour_local: int
    hour_start: str
    n_headways: int
    pct_within_band: float | None
    pct_bunched: float | None
    pct_gap: float | None
    scheduled_headways: float
    coverage_pct: float | None
    data_outage: bool
    status: Literal["measured", "withheld", "no_headways", "outage"]
    withheld_reason: str | None


class SpacingData(Row):
    line: str
    service_date: str
    hours: list[SpacingHour]


class DisruptionSegment(Row):
    direction_id: int
    from_stop_id: str
    to_stop_id: str


class DisruptionRow(Row):
    alert_id: str
    effect: str
    header_text: str | None
    first_seen: str
    last_seen: str
    is_active: bool
    n_stations: int
    from_station_name: str | None
    to_station_name: str | None
    segments: list[DisruptionSegment]


class DisruptionsData(Row):
    line: str
    window: str
    rows: list[DisruptionRow]


class QualityCheck(Row):
    check_name: str
    n_hours_evaluated: int
    n_fail_hours: int
    n_warn_hours: int
    n_ok_hours: int
    worst_status: Literal["fail", "warn", "ok", "not_evaluated"]


class ChecksData(Row):
    hours: int
    n_checks: int
    n_failing_checks: int
    n_warning_checks: int
    checks: list[QualityCheck]


# route -> the data model of its 200 answer (the path as FastAPI registers it)
ROUTE_DATA: dict[str, Any] = {
    "/v1/lines": list[LineSummary],
    "/v1/lines/{line}/reliability": ReliabilityData,
    "/v1/lines/{line}/accuracy": AccuracyData,
    "/v1/lines/{line}/headways": HeadwayData,
    "/v1/lines/{line}/countdown": CountdownData,
    "/v1/lines/{line}/segments": SegmentsData,
    "/v1/lines/{line}/marey": MareyData,
    "/v1/lines/{line}/trip-time": TripTimeData,
    "/v1/lines/{line}/trip-time-budget": BudgetData,
    "/v1/lines/{line}/spacing-by-hour": SpacingData,
    "/v1/lines/{line}/disruptions": DisruptionsData,
    "/v1/slow-zones": SlowZonesData,
    "/v1/accessibility": AccessibilityData,
    "/v1/incidents": IncidentsData,
    "/v1/forecast/{line}": ForecastData,
    "/v1/pipeline/health": HealthData,
    "/v1/pipeline/heartbeat": HeartbeatData,
    "/v1/pipeline/checks": ChecksData,
    "/v1/slo": SloData,
    "/v1/metrics": list[MetricDef],
    "/v1/meta": MetaData,
}
