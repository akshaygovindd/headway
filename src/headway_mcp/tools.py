"""The five analyst tools. Read-only, fixed SQL over the public gold tables, no free SQL.

Every call validates its enums and dates, builds the SQL from them (so the SQL shown in the
provenance is exactly what ran), runs it with a row limit and a timeout, and returns::

    {"status": "ok", "rows": [...], "provenance": {...}}

or a structured ``not_available`` (with the metrics this export can answer), ``invalid_input``,
``timeout`` or ``data_error``. A value from a row whose arrival coverage is under 50% is withheld
(null), as on every page. Each call writes one audit line.
"""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

from headway_mcp import audit, config, runner
from headway_mcp.catalog import (
    GRANULARITIES,
    HISTORY_VERSIONS,
    LINES,
    METRICS,
    METRICS_DOC,
    Metric,
    Source,
)

Result = dict[str, Any]
TOOL_NAMES = (
    "get_line_reliability",
    "compare_lines",
    "get_delay_trends",
    "get_service_alerts",
    "get_pipeline_health",
)


class InputError(ValueError):
    def __init__(self, message: str, allowed: list[str] | None = None) -> None:
        super().__init__(message)
        self.allowed = allowed


class NotAvailableError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _check_line(line: object) -> str:
    if line not in LINES:
        raise InputError(f"unknown line {line!r}", list(LINES))
    return str(line)


def _check_metric(metric: object) -> Metric:
    if not isinstance(metric, str) or metric not in METRICS:
        raise InputError(f"unknown metric {metric!r}", list(METRICS))
    return METRICS[metric]


def _day(value: object, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise InputError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def _range(date_range: object, max_days: int) -> tuple[date, date]:
    if not isinstance(date_range, dict) or set(date_range) != {"start", "end"}:
        raise InputError('range must be {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}')
    start, end = _day(date_range["start"], "start"), _day(date_range["end"], "end")
    if end < start:
        raise InputError("range end is before its start")
    if (end - start).days + 1 > max_days:
        raise InputError(f"range is longer than {max_days} days")
    return start, end


def _sql_list(items: list[str]) -> str:
    return ", ".join(f"'{i}'" for i in items)  # items are validated enums or dates


class Tools:
    """The tools over one data root. `data_url` is https:// or file:// (tests)."""

    def __init__(self, data_url: str | None = None) -> None:
        self.base = config.base_url(data_url)

    # --- shared ----------------------------------------------------------------------------

    def _manifest(self) -> dict[str, Any]:
        return runner.read_json(self.base, "gold/manifest.json")

    def available_metrics(self, manifest: dict[str, Any]) -> list[str]:
        tables = set(manifest.get("row_counts", {}))
        return [
            m.name
            for m in METRICS.values()
            if any(s is not None and s.table in tables for s in (m.window, m.daily, m.hourly))
        ]

    def _source(self, metric: Metric, kind: str, manifest: dict[str, Any]) -> Source:
        source: Source | None = getattr(metric, kind)
        if source is None:
            kinds = [k for k in ("window", "daily", "hourly") if getattr(metric, k)]
            raise NotAvailableError(
                f"{metric.name} has no {kind} table; it is available as: {', '.join(kinds)}"
            )
        if source.table not in manifest.get("row_counts", {}):
            raise NotAvailableError(f"{source.table} is not in this export")
        return source

    def _value_sql(self, metric: Metric, s: Source) -> tuple[str, str]:
        """The masked value expression and the coverage expression of a source."""
        raw = f"{s.scale} {s.column}".strip()
        if s.column == "coverage_pct":
            return "round(coverage_pct, 1)", "round(coverage_pct, 1)"
        masked = f"round(case when coverage_pct >= {config.MIN_COVERAGE_PCT:g} then {raw} end, 1)"
        return masked, "round(coverage_pct, 1)"

    def _finish(
        self,
        tool: str,
        args: dict[str, Any],
        fn: Callable[[dict[str, Any]], Result],
    ) -> Result:
        started = time.perf_counter()
        status, rows = "ok", 0
        try:
            manifest = self._manifest()
            result = fn(manifest)
            rows = len(result["rows"])
            result["provenance"]["freshness"] = {
                "exported_at": manifest.get("exported_at"),
                "metrics_as_of": manifest.get("metrics_as_of"),
            }
        except InputError as exc:
            status = "invalid_input"
            result = {"status": status, "error": str(exc), "allowed": exc.allowed}
        except NotAvailableError as exc:
            status = "not_available"
            result = {
                "status": status,
                "reason": exc.reason,
                "available_metrics": self.available_metrics(manifest),
            }
        except runner.ToolTimeoutError as exc:
            status = "timeout"
            result = {"status": status, "error": str(exc)}
        except runner.DataError as exc:
            status = "data_error"
            result = {"status": status, "error": str(exc)}
        audit.log_call(tool, args, rows, (time.perf_counter() - started) * 1000, status)
        return result

    def _query(self, sql: str) -> tuple[list[dict[str, Any]], bool]:
        cols, fetched = runner.run(self.base, sql)
        truncated = len(fetched) > config.MAX_ROWS
        rows = [dict(zip(cols, r, strict=True)) for r in fetched[: config.MAX_ROWS]]
        return rows, truncated

    def _provenance(
        self,
        metric: Metric | None,
        tables: list[str],
        sql: str,
        rows: list[dict[str, Any]],
        truncated: bool,
        period: dict[str, Any],
        anchor: str | None = None,
        definition: str | None = None,
    ) -> dict[str, Any]:
        cov = [r["coverage_pct"] for r in rows if r.get("coverage_pct") is not None]
        return {
            "tables": tables,
            "metric": metric.name if metric else None,
            "definition": definition or (metric.label if metric else None),
            "metrics_md": f"{METRICS_DOC}#{anchor or (metric.anchor if metric else '')}",
            "comparison_period": period,
            "coverage_pct": round(sum(cov) / len(cov), 1) if cov else None,
            "min_coverage_pct": min(cov) if cov else None,
            "row_limit": config.MAX_ROWS,
            "truncated": truncated,
            "sql": sql,
        }

    def _window_rows(
        self, tool: str, args: dict[str, Any], lines: list[str], metric_name: object, window: object
    ) -> Result:
        def go(manifest: dict[str, Any]) -> Result:
            metric = _check_metric(metric_name)
            if window not in ("last_24h", "last_7d"):
                raise InputError(f"unknown window {window!r}", ["last_24h", "last_7d"])
            if window not in metric.windows:
                raise NotAvailableError(
                    f"{metric.name} is only available for window: {', '.join(metric.windows)}"
                )
            s = self._source(metric, "window", manifest)
            value, cov = self._value_sql(metric, s)
            cov_col = "" if s.column == "coverage_pct" else f", {cov} as coverage_pct"
            sql = (
                f"select line, window_label as window, {value} as {metric.name}{cov_col}, "
                f"{s.n_column} as n_observations "
                f"from read_parquet('{runner.path_of(self.base, f'gold/{s.table}.parquet')}') "
                f"where window_label = '{window}' and line in ({_sql_list(lines)}) "
                f"order by line limit {config.MAX_ROWS + 1}"
            )
            rows, truncated = self._query(sql)
            if len(lines) > 1:
                order = {name: i for i, name in enumerate(lines)}
                rows.sort(key=lambda r: order[r["line"]])
                base_v = rows[0][metric.name] if rows else None
                for r in rows:
                    v = r[metric.name]
                    r[f"delta_{metric.name}"] = (
                        round(v - base_v, 1) if v is not None and base_v is not None else None
                    )
            period = {"window": window, "kind": "rolling window ending at the latest export"}
            prov = self._provenance(metric, [s.table], sql, rows, truncated, period)
            if len(lines) > 1:
                prov["derived"] = (
                    f"delta_{metric.name} = value minus the value of {lines[0]} (first listed)"
                )
            return {"status": "ok", "rows": rows, "provenance": prov}

        return self._finish(tool, args, go)

    # --- the five tools --------------------------------------------------------------------

    def get_line_reliability(self, line: str, metric: str, window: str) -> Result:
        args = {"line": line, "metric": metric, "window": window}
        try:
            _check_line(line)
        except InputError as exc:
            return self._invalid("get_line_reliability", args, exc)
        return self._window_rows("get_line_reliability", args, [line], metric, window)

    def compare_lines(self, lines: list[str], metric: str, window: str) -> Result:
        args = {"lines": lines, "metric": metric, "window": window}
        try:
            if not isinstance(lines, list) or not 2 <= len(lines) <= len(LINES):
                raise InputError("lines must list between 2 and 8 lines", list(LINES))
            for line in lines:
                _check_line(line)
            if len(set(lines)) != len(lines):
                raise InputError("lines must not repeat")
        except InputError as exc:
            return self._invalid("compare_lines", args, exc)
        return self._window_rows("compare_lines", args, list(lines), metric, window)

    def get_delay_trends(
        self, line: str, metric: str, granularity: str, date_range: dict[str, str]
    ) -> Result:
        args = {"line": line, "metric": metric, "granularity": granularity, "range": date_range}

        def go(manifest: dict[str, Any]) -> Result:
            _check_line(line)
            m = _check_metric(metric)
            if granularity not in GRANULARITIES:
                raise InputError(f"unknown granularity {granularity!r}", list(GRANULARITIES))
            start, end = _range(date_range, config.MAX_RANGE_DAYS)
            s = self._source(m, granularity, manifest)
            value, cov = self._value_sql(m, s)
            hourly = granularity == "hourly"
            src, tables = self._trend_source(s, start, end)
            cov_col = "" if s.column == "coverage_pct" else f", {cov} as coverage_pct"
            hour_col = (
                ", lpad(cast(hour_local as varchar), 2, '0') || ':00' as hour" if hourly else ""
            )
            extras = "".join(f", {e}" for e in s.extras)
            order = "date, hour" if hourly else "date"
            part = ", ".join(["direction_id", *s.extras])
            sql = (
                f"with t as (select line, direction_id, "
                f"strftime(cast(service_date as date), '%Y-%m-%d') as date{hour_col}{extras}, "
                f"{value} as {m.name}{cov_col}, {s.n_column} as n_observations "
                f"from {src} where line = '{line}' and cast(service_date as date) "
                f"between '{start}' and '{end}') "
                f"select *, round({m.name} - lag({m.name}) over (partition by {part} "
                f"order by {order}), 1) as delta_{m.name} from t "
                f"order by {order}, {part} limit {config.MAX_ROWS + 1}"
            )
            rows, truncated = self._query(sql)
            period = {"start_date": start.isoformat(), "end_date": end.isoformat(),
                      "granularity": granularity}  # fmt: skip
            prov = self._provenance(m, tables, sql, rows, truncated, period)
            prov["derived"] = f"delta_{m.name} = value minus the previous row of the same series"
            return {"status": "ok", "rows": rows, "provenance": prov}

        return self._finish("get_delay_trends", args, go)

    def get_service_alerts(self, line: str, date_range: dict[str, str]) -> Result:
        args = {"line": line, "range": date_range}

        def go(manifest: dict[str, Any]) -> Result:
            _check_line(line)
            start, end = _range(date_range, config.MAX_SHORT_RANGE_DAYS)
            table = "agg_active_alerts"
            if table not in manifest.get("row_counts", {}):
                raise NotAvailableError(f"{table} is not in this export")
            sql = (
                "select strftime(cast(service_date as date), '%Y-%m-%d') as date, effect, scope, "
                "is_service_disruption, max(n_active_alerts) as max_active_alerts, "
                "count(distinct hour_local) as hours_active "
                f"from read_parquet('{runner.path_of(self.base, f'gold/{table}.parquet')}') "
                f"where line = '{line}' and cast(service_date as date) "
                f"between '{start}' and '{end}' group by 1, 2, 3, 4 "
                f"order by 1, 2, 3 limit {config.MAX_ROWS + 1}"
            )
            rows, truncated = self._query(sql)
            period = {"start_date": start.isoformat(), "end_date": end.isoformat()}
            prov = self._provenance(
                None, [table], sql, rows, truncated, period,
                anchor="release-2-metrics-shared-rules",
                definition="Service alerts active per line (effect, scope, disruption label)",
            )  # fmt: skip
            return {"status": "ok", "rows": rows, "provenance": prov}

        return self._finish("get_service_alerts", args, go)

    def get_pipeline_health(self, date_range: dict[str, str]) -> Result:
        args = {"range": date_range}

        def go(manifest: dict[str, Any]) -> Result:
            start, end = _range(date_range, config.MAX_SHORT_RANGE_DAYS)
            table = "agg_pipeline_health"
            if table not in manifest.get("row_counts", {}):
                raise NotAvailableError(f"{table} is not in this export")
            local = "cast(scheduled_at at time zone 'America/New_York' as date)"
            sql = (
                f"select strftime({local}, '%Y-%m-%d') as date, count(*) as runs, "
                "sum(vehicle_polls_ok) as vehicle_polls_ok, "
                "sum(vehicle_polls_error) as vehicle_polls_error, "
                "sum(duplicate_delivery_count) as duplicate_deliveries, "
                "sum(quarantine_total) as quarantined_records, "
                "count(*) filter (where trip_updates_status <> 'ok') as trip_update_failures "
                f"from read_parquet('{runner.path_of(self.base, f'gold/{table}.parquet')}') "
                f"where {local} between '{start}' and '{end}' group by 1 "
                f"order by 1 limit {config.MAX_ROWS + 1}"
            )
            rows, truncated = self._query(sql)
            period = {"start_date": start.isoformat(), "end_date": end.isoformat()}
            prov = self._provenance(
                None, [table], sql, rows, truncated, period, anchor="4-freshness",
                definition="Pipeline runs, feed polls, duplicates and quarantined records",
            )  # fmt: skip
            return {"status": "ok", "rows": rows, "provenance": prov}

        return self._finish("get_pipeline_health", args, go)

    # --- helpers ---------------------------------------------------------------------------

    def _invalid(self, tool: str, args: dict[str, Any], exc: InputError) -> Result:
        audit.log_call(tool, args, 0, 0.0, "invalid_input")
        return {"status": "invalid_input", "error": str(exc), "allowed": exc.allowed}

    def _trend_source(self, s: Source, start: date, end: date) -> tuple[str, list[str]]:
        """The table to read: the gold file, plus history days of the range when they exist."""
        gold = f"read_parquet('{runner.path_of(self.base, f'gold/{s.table}.parquet')}')"
        if s.history_family is None:
            return gold, [s.table]
        version = HISTORY_VERSIONS[s.history_family]
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        rels = [
            f"history/table={s.table}/metric_version={version}/service_date={d}/part.parquet"
            for d in days
        ]
        with ThreadPoolExecutor(max_workers=8) as pool:
            found = [r for r, ok in zip(rels, pool.map(lambda r: runner.exists(self.base, r), rels),
                                        strict=True) if ok]  # fmt: skip
        if not found:
            return gold, [s.table]
        files = ", ".join(f"'{runner.path_of(self.base, r)}'" for r in found)
        hour = ["hour_local"] if s.table.endswith("hourly") else []
        key = ", ".join(["line", "direction_id", "service_date", *hour, *s.extras])
        src = (
            f"(select * exclude (_src) from (select *, 1 as _src from {gold} "
            f"union all by name select *, 2 as _src from read_parquet([{files}], "
            f"union_by_name = true)) qualify row_number() over (partition by {key} "
            f"order by _src) = 1)"
        )
        return src, [s.table, f"history/{s.table}"]
