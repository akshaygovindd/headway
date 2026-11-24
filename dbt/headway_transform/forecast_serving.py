"""Forecast serving in the transform (Release 3, ADR-019); only with HEADWAY_ENABLE_R3=true.

Two steps around the dbt build:

* ``load_reports`` (before the build) puts the training job's summaries
  (``models/forecast/reports/*.parquet``) into the warehouse as ``raw_forecast_backtest`` and
  ``raw_forecast_gate_status``, which ``agg_forecast_backtest`` and ``agg_forecast_gate_status``
  read. Without them (no training run yet) the tables are dropped and those models are empty.
* ``serve`` (after the build, before the export) scores the features of the next hour
  (``int_forecast_serving_features``) with the registered model, once per cold start (the model
  files are cached in /tmp), and writes the result into ``agg_forecast_next_hour``. A line is
  scored only when it is in the approved model AND past the training gate; every other line keeps
  the baselines with the reason. No ``latest.json``, an unreadable model or an error all end in
  baselines only: serving never fails the run, and never reports a model forecast it did not make.
"""

import importlib
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger()

REPORTS = {
    "raw_forecast_backtest": "models/forecast/reports/backtest.parquet",
    "raw_forecast_gate_status": "models/forecast/reports/gate_status.parquet",
}
SCORES = "forecast_serving_scores"
SLOT = ["line", "direction_id", "stop_id", "prediction_ts", "horizon_min"]


def load_reports(s3: Any, bucket: str, db_path: str) -> dict[str, int]:
    """Rows loaded per report table (0 when the training job has not written it)."""
    loaded: dict[str, int] = {}
    con = duckdb.connect(db_path)
    try:
        for table, key in REPORTS.items():
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "report.parquet"
                try:
                    s3.download_file(bucket, key, str(path))
                except s3.exceptions.ClientError as exc:
                    if exc.response["Error"]["Code"] not in {"404", "NoSuchKey"}:
                        raise
                    con.execute(f"drop table if exists main.{table}")
                    loaded[table] = 0
                    continue
                con.execute(
                    f"create or replace table main.{table} as "  # noqa: S608
                    f"select * from read_parquet('{path}')"
                )
                row = con.execute(f"select count(*) from main.{table}").fetchone()  # noqa: S608
                loaded[table] = int(row[0]) if row else 0
    finally:
        con.close()
    return loaded


def _default_loader() -> Callable[[Any, str], Any]:
    """headway_ml is copied next to this package in the image; imported lazily so the modules
    that need LightGBM load only when a model is served."""
    return importlib.import_module("headway_ml.serving").load_latest  # type: ignore[no-any-return]


def serve(
    s3: Any, bucket: str, db_path: str, load_model: Callable[[Any, str], Any] | None = None
) -> dict[str, Any]:
    """Fill agg_forecast_next_hour's model columns. Returns the summary of the step."""
    con = duckdb.connect(db_path)
    try:
        con.execute("set TimeZone = 'UTC'")
        tables = {
            r[0]
            for r in con.execute(
                "select table_name from information_schema.tables where table_schema = 'main'"
            ).fetchall()
        }
        if "agg_forecast_next_hour" not in tables:
            return {"forecast_serving_status": "not_built"}
        passed = {
            r[0]
            for r in con.execute(
                "select line from agg_forecast_gate_status where gate_passed"
            ).fetchall()
        }
        model: Any = None
        error: str | None = None
        try:
            model = (load_model or _default_loader())(s3, bucket)
        except Exception as exc:  # unreadable model: baselines only, never a failed run
            logger.exception("forecast model could not be loaded")
            error = f"{type(exc).__name__}"
        scored = _score(con, model, passed) if model is not None else 0
        _label_baselines(con, model, passed, error)
        total, with_model = con.execute(
            "select count(*), count(model_forecast_s) from agg_forecast_next_hour"
        ).fetchone() or (0, 0)
    finally:
        con.close()
    return {
        "forecast_serving_status": "model" if with_model else "baselines_only",
        "forecast_model_rows": int(with_model),
        "forecast_baseline_rows": int(total),
        "forecast_model_version": None if model is None else int(model.version),
        "forecast_model_from_cache": None if model is None else bool(model.from_cache),
        "forecast_scored_trip_stops": scored,
        "forecast_model_error": error,
    }


def _score(con: Any, model: Any, passed: set[str]) -> int:
    lines = sorted(set(model.lines) & passed)
    if not lines:
        return 0
    marks = ", ".join("?" for _ in lines)
    rows = con.execute(
        f"select * from int_forecast_serving_features where line in ({marks})",  # noqa: S608
        lines,
    ).df()
    if rows.empty:
        return 0
    rows["score"] = model.score(rows)
    slots = rows.groupby(SLOT, dropna=False)["score"].agg(["mean", "count"]).reset_index()
    con.register("slot_scores", slots)
    con.execute(f"create or replace table main.{SCORES} as select * from slot_scores")  # noqa: S608
    con.unregister("slot_scores")
    con.execute(
        f"""
        update agg_forecast_next_hour a
        set model_forecast_s = s."mean", n_model = s."count", forecast_status = 'model',
            status_reason = 'model v{int(model.version)}', model_version = {int(model.version)}
        from main.{SCORES} s
        where a.line = s.line and a.direction_id is not distinct from s.direction_id
            and a.stop_id = s.stop_id and a.prediction_ts = s.prediction_ts
            and a.horizon_min = s.horizon_min
        """  # noqa: S608
    )
    return len(rows)


def _label_baselines(con: Any, model: Any, passed: set[str], error: str | None) -> None:
    """The reason on every row that has no model forecast."""
    in_model = [] if model is None else sorted(model.lines)

    def missing_from(items: list[str] | set[str]) -> str:
        """SQL condition: the line is not one of items (always true for none)."""
        if not items:
            return "true"
        quoted = ", ".join("'" + str(i).replace("'", "''") + "'" for i in sorted(items))
        return f"line not in ({quoted})"

    if model is None:
        fallback = (
            f"model unavailable ({error})" if error else "no approved model yet (no latest.json)"
        )
    else:
        fallback = "line is not in the approved model"
    con.execute(
        f"""
        update agg_forecast_next_hour
        set status_reason = case
            when {missing_from(passed)} then 'training gate not passed'
            when {missing_from(in_model)} then '{fallback}'
            else status_reason end
        where forecast_status = 'baselines_only'
        """  # noqa: S608
    )
