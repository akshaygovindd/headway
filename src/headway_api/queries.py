"""Fixed SQL over the allowlisted gold tables. Every value in the SQL is a validated enum, a
validated date or a number; nothing a caller types reaches the query text.

A value from a row whose coverage is under 50% is null with a reason (the rule of every page).
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from headway_api.cache import GoldSource, Snapshot
from headway_mcp import config, runner
from headway_mcp.catalog import LINES, METRICS, METRICS_DOC

MIN_COV = config.MIN_COVERAGE_PCT
WINDOWS = ("last_24h", "last_7d")
MAX_INCIDENT_DAYS = 31
MAX_HEALTH_DAYS = 30
TZ = "America/New_York"


class InputError(ValueError):
    def __init__(self, message: str, allowed: list[str] | None = None) -> None:
        super().__init__(message)
        self.allowed = allowed


class NotAvailableError(RuntimeError):
    def __init__(self, reason: str, tables: list[str]) -> None:
        super().__init__(reason)
        self.reason, self.tables = reason, tables


@dataclass(frozen=True)
class Section:
    name: str
    table: str
    keys: tuple[str, ...]
    values: tuple[str, ...]
    n_column: str
    anchor: str
    label: str


SECTIONS: dict[str, tuple[Section, ...]] = {
    "reliability": (
        Section(
            "predicted_deviation",
            "agg_line_reliability_window",
            (),
            (
                "median_predicted_deviation_s",
                "p90_predicted_deviation_s",
                "median_abs_revision_s",
                "skipped_share",
            ),
            "n_eligible",
            "1-predicted-schedule-deviation",
            "Predicted schedule deviation and prediction revision (last_24h only)",
        ),
        Section(
            "actual_deviation",
            "agg_actual_deviation_window",
            (),
            ("median_deviation_s", "p90_deviation_s", "pct_on_time_window"),
            "n_arrivals",
            "7-actual-schedule-deviation-release-2",
            "Actual schedule deviation and on-time share",
        ),
    ),  # fmt: skip
    "accuracy": (
        Section(
            "prediction_accuracy",
            "agg_accuracy_window",
            ("horizon_bucket",),
            ("median_abs_error_s", "p90_abs_error_s", "mae_s", "pct_within_60s", "pct_within_120s"),
            "n",
            "release-2-metrics-shared-rules",
            "Accuracy of MBTA's published predictions by horizon bucket",
        ),
    ),  # fmt: skip
    "headways": (
        Section(
            "headway_adherence",
            "agg_headway_window",
            (),
            ("pct_within_band", "pct_bunched", "pct_gap", "median_ratio", "p90_ratio"),
            "n_headways",
            "8-headway-adherence-release-2",
            "Headways within the band, bunched and gaps",
        ),
    ),  # fmt: skip
    "countdown": (
        Section(
            "countdown_accuracy",
            "agg_countdown_accuracy_window",
            ("station_id", "station_name", "countdown_min"),
            ("pct_within_60s", "pct_within_120s"),
            "n",
            "release-2-metrics-shared-rules",
            "Countdown accuracy of MBTA's published predictions by station",
        ),
    ),  # fmt: skip
}

FORECAST_METHODS = (
    ("model_forecast_s", "n_model"),
    ("persistence", "n_persistence"),
    ("daytype_hour", "n_daytype_hour"),
    ("rolling_median_60m", "n_rolling_median_60m"),
    ("mbta_reference", "n_mbta_reference"),
)
API_TABLES = tuple(
    sorted(
        {s.table for secs in SECTIONS.values() for s in secs}
        | {"agg_incidents", "agg_forecast_next_hour", "agg_forecast_gate_status",
           "agg_slo_rollup", "agg_pipeline_health"}
    )
)  # fmt: skip


def _jsonable(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    return v


def _day(value: str | None, name: str) -> dt.date | None:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise InputError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


@dataclass
class Result:
    data: Any
    tables: list[str]
    sql: list[str]
    anchor: str
    definition: str
    rows: list[dict[str, Any]]  # the rows coverage is computed from
    truncated: bool = False
    metric: str | None = None


class Query:
    """One request: a snapshot of the export, then fixed queries on it."""

    def __init__(self, source: GoldSource, max_rows: int) -> None:
        self.source, self.max_rows = source, max_rows
        self.snap: Snapshot = source.snapshot()
        self.tables = set(self.snap.manifest.get("row_counts", {}))

    # --- plumbing -------------------------------------------------------------------------

    def path(self, base: str, table: str) -> str:
        return runner.path_of(base, f"gold/{table}.parquet")

    def need(self, *tables: str) -> None:
        missing = [t for t in tables if t not in self.tables]
        if missing:
            raise NotAvailableError(
                f"{', '.join(missing)} is not in this export", sorted(self.tables)
            )

    def run(self, build: Callable[[str], str]) -> tuple[list[dict[str, Any]], bool, str]:
        """Rows (at most max_rows), whether more existed, and the SQL against the public URL."""
        base = self.snap.base
        try:
            cols, fetched = runner.run(base, build(base))
        except runner.DataError:
            if base == self.snap.remote:
                raise
            self.source.fallback()  # the local copy failed: read the public objects
            self.snap = replace(self.snap, base=self.snap.remote, served_from="remote")
            cols, fetched = runner.run(self.snap.remote, build(self.snap.remote))
        rows = [
            {c: _jsonable(v) for c, v in zip(cols, r, strict=True)}
            for r in fetched[: self.max_rows]
        ]
        return rows, len(fetched) > self.max_rows, build(self.snap.remote)

    @staticmethod
    def _reason(row: dict[str, Any]) -> str | None:
        cov = row.get("coverage_pct")
        if cov is None:
            return "no coverage figure: the scheduled denominator is zero"
        return f"coverage {cov:g}% is below {MIN_COV:g}%" if cov < MIN_COV else None

    # --- window sections ------------------------------------------------------------------

    def _section_sql(self, s: Section, base: str, line: str | None, window: str) -> str:
        ok = f"coalesce(coverage_pct, 0) >= {MIN_COV:g}"
        vals = ", ".join(f"round(case when {ok} then {v} end, 2) as {v}" for v in s.values)
        keys = "".join(f", {k}" for k in s.keys)
        where = f"window_label = '{window}'" + (f" and line = '{line}'" if line else "")
        order = ", ".join(["line", *s.keys])
        return (
            f"select line{keys}, window_label as window, {vals}, {s.n_column} as n_observations, "
            f"round(coverage_pct, 1) as coverage_pct, not ({ok}) as withheld "
            f"from read_parquet('{self.path(base, s.table)}') where {where} "
            f"order by {order} limit {self.max_rows + 1}"
        )

    def sections(self, endpoint: str, line: str | None, window: str) -> Result:
        secs = SECTIONS[endpoint]
        present = [s for s in secs if s.table in self.tables]
        if not present:
            raise NotAvailableError(
                f"{', '.join(s.table for s in secs)} not in this export", sorted(self.tables)
            )
        data: dict[str, Any] = {}
        sqls: list[str] = []
        all_rows: list[dict[str, Any]] = []
        truncated = False
        for s in present:

            def build(base: str, s: Section = s) -> str:
                return self._section_sql(s, base, line, window)

            rows, more, shown = self.run(build)
            for r in rows:
                r["withheld_reason"] = self._reason(r) if r.pop("withheld") else None
            all_rows += rows
            truncated |= more
            sqls.append(shown)
            data[s.name] = {"table": s.table, "label": s.label, "metrics_md": s.anchor,
                            "rows": rows}  # fmt: skip
        missing = [s.table for s in secs if s.table not in self.tables]
        out = {"line": line, "window": window, **data}
        if missing:
            out["unavailable_tables"] = missing
        return Result(out, [s.table for s in present], sqls, present[0].anchor,
                      present[0].label, all_rows, truncated)  # fmt: skip

    def lines(self) -> Result:
        res = self.sections("reliability", None, "last_24h")
        by_line = (
            {r["line"]: r for r in res.data["predicted_deviation"]["rows"]}
            if "predicted_deviation" in res.data
            else {}
        )
        data = []
        for ln in LINES:
            r = by_line.get(ln)
            data.append({
                "line": ln,
                "coverage_pct_last_24h": r["coverage_pct"] if r else None,
                "withheld_last_24h": r["withheld_reason"] is not None if r else None,
                "withheld_reason": r["withheld_reason"] if r else "no row for this line",
                "endpoints": [f"/v1/lines/{ln}/{e}" for e in SECTIONS],
            })  # fmt: skip
        res.data = data
        return res

    # --- other endpoints ------------------------------------------------------------------

    def incidents(self, line: str | None, from_: str | None, to: str | None) -> Result:
        self.need("agg_incidents")
        as_of = dt.date.fromisoformat(str(self.snap.manifest.get("metrics_as_of", ""))[:10])
        end = _day(to, "to") or as_of
        start = _day(from_, "from") or end - dt.timedelta(days=MAX_INCIDENT_DAYS - 1)
        if end < start:
            raise InputError("from is after to")
        if (end - start).days + 1 > MAX_INCIDENT_DAYS:
            raise InputError(f"range is longer than {MAX_INCIDENT_DAYS} days")
        cols = ("incident_id, line, direction_id, cast(incident_start_ts as varchar) as start_ts, "
                "cast(incident_end_ts as varchar) as end_ts, n_hours, is_open, thresholds_crossed, "
                "round(min_coverage_pct, 1) as min_coverage_pct, "
                "round(mean_coverage_pct, 1) as mean_coverage_pct, n_outage_hours_inside, "
                "match_status, alert_lag_s, summary_text, template_used")  # fmt: skip

        def build(base: str) -> str:
            ln = f" and line = '{line}'" if line else ""
            return (
                f"select {cols} from read_parquet('{self.path(base, 'agg_incidents')}') "
                f"where cast(incident_start_ts at time zone '{TZ}' as date) "
                f"between '{start}' and '{end}'{ln} order by incident_start_ts desc, incident_id "
                f"limit {self.max_rows + 1}"
            )

        rows, more, shown = self.run(build)
        for r in rows:
            r["coverage_pct"] = r["mean_coverage_pct"]
        data = {"line": line, "from": start.isoformat(), "to": end.isoformat(), "incidents": rows}
        return Result(data, ["agg_incidents"], [shown], "release-3-metrics",
                      "Incidents: line-hours that crossed a reliability threshold, with their "
                      "evidence-checked summary", rows, more)  # fmt: skip

    def forecast(self, line: str) -> Result:
        self.need("agg_forecast_next_hour")
        parts, outs = [], []
        for col, n in FORECAST_METHODS:
            cov = f"100.0 * sum({n}) / nullif(sum(n_trips), 0)"
            parts.append(
                f"round(case when {cov} >= {MIN_COV:g} then "
                f"sum({col} * {n}) / nullif(sum({n}), 0) end, 1) as {col}, "
                f"round({cov}, 1) as {col}_coverage_pct"
            )
            outs.append(col)

        def build(base: str) -> str:
            p = self.path(base, "agg_forecast_next_hour")
            return (
                f"select direction_id, horizon_min, cast(any_value(prediction_ts) as varchar) "
                f"as prediction_ts, sum(n_trips) as n_trips, {', '.join(parts)}, "
                f"any_value(forecast_status) as forecast_status, "
                f"any_value(status_reason) as status_reason, any_value(model_version) as "
                f"model_version from read_parquet('{p}') where line = '{line}' and prediction_ts "
                f"= (select max(prediction_ts) from read_parquet('{p}') where line = '{line}') "
                f"group by direction_id, horizon_min order by direction_id, horizon_min "
                f"limit {self.max_rows + 1}"
            )

        rows, more, shown = self.run(build)
        covs: list[dict[str, Any]] = []
        for r in rows:
            reasons: dict[str, str] = {}
            for col in outs:
                c = r[f"{col}_coverage_pct"]
                if r[col] is None:
                    reasons[col] = (f"coverage {c:g}% is below {MIN_COV:g}%" if c is not None
                                    else "no trips with this input")  # fmt: skip
            r["withheld_reasons"] = reasons
            covs.append({"coverage_pct": min(
                (r[f"{c}_coverage_pct"] for c in outs if r[f"{c}_coverage_pct"] is not None),
                default=None)})  # fmt: skip
        tables = ["agg_forecast_next_hour"]
        sqls = [shown]
        gate: list[dict[str, Any]] = []
        if "agg_forecast_gate_status" in self.tables:
            tables.append("agg_forecast_gate_status")

            def gate_sql(base: str) -> str:
                return (
                    "select line, n_days, n_weekend_days, min_days, min_weekend_days, gate_passed, "
                    f"source from read_parquet('{self.path(base, 'agg_forecast_gate_status')}') "
                    f"where line = '{line}'"
                )

            gate, _, gshown = self.run(gate_sql)
            sqls.append(gshown)
        head = rows[0] if rows else {}
        data = {
            "line": line,
            "prediction_ts": head.get("prediction_ts"),
            "status": head.get("forecast_status"),
            "status_reason": head.get("status_reason"),
            "model_version": head.get("model_version"),
            "unit": "seconds of delay against the schedule, trip-weighted",
            "horizons": rows,
            "training_gate": gate,
        }
        return Result(data, tables, sqls, "release-3-metrics",
                      "Next-hour delay forecasts: model (when approved) and baselines",
                      covs, more)  # fmt: skip

    def slo(self) -> Result:
        self.need("agg_slo_rollup")

        def build(base: str) -> str:
            return (
                "select slo_name, window_label as window, target_pct, good_events, total_events, "
                "round(actual_pct, 2) as actual_pct, met, round(error_budget_used, 3) as "
                "error_budget_used, days_covered, counts_toward_slo from "
                f"read_parquet('{self.path(base, 'agg_slo_rollup')}') "
                f"order by slo_name, window_label limit {self.max_rows + 1}"
            )

        rows, more, shown = self.run(build)
        return Result({"slos": rows}, ["agg_slo_rollup"], [shown], "release-2-slos",
                      "Service level objectives over 7 and 30 days", rows, more)  # fmt: skip

    def health(self, days: int) -> Result:
        self.need("agg_pipeline_health")
        if not 1 <= days <= MAX_HEALTH_DAYS:
            raise InputError(f"days must be between 1 and {MAX_HEALTH_DAYS}")

        def build(base: str) -> str:
            p = self.path(base, "agg_pipeline_health")
            local = f"cast(scheduled_at at time zone '{TZ}' as date)"
            return (
                f"with t as (select {local} as d, * from read_parquet('{p}')) "
                "select strftime(d, '%Y-%m-%d') as date, count(*) as runs, "
                "sum(vehicle_polls_ok) as vehicle_polls_ok, "
                "sum(vehicle_polls_error) as vehicle_polls_error, "
                "sum(duplicate_delivery_count) as duplicate_deliveries, "
                "sum(quarantine_total) as quarantined_records, "
                "count(*) filter (where trip_updates_status <> 'ok') as trip_update_failures "
                f"from t where d > (select max(d) from t) - {int(days)} group by d "
                f"order by d limit {self.max_rows + 1}"
            )

        rows, more, shown = self.run(build)
        return Result({"days": days, "daily": rows}, ["agg_pipeline_health"], [shown],
                      "4-freshness", "Pipeline runs, feed polls, duplicates and quarantine",
                      rows, more)  # fmt: skip


def metrics_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": m.name, "label": m.label, "higher_is_worse": m.higher_is_worse,
            "windows": list(m.windows),
            "tables": sorted({s.table for s in (m.window, m.daily, m.hourly) if s}),
            "metrics_md": f"{METRICS_DOC}#{m.anchor}",
        }
        for m in METRICS.values()
    ]  # fmt: skip
