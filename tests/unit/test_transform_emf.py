import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from headway_transform import emf, handler

NOW = datetime(2026, 11, 23, 15, 20, tzinfo=UTC)  # 10:20 local
LAG = timedelta(minutes=30)


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = str(tmp_path / "h.duckdb")
    con = duckdb.connect(path)
    con.execute("set TimeZone = 'UTC'")
    con.execute("create table agg_transform_runs (status varchar, gold_exported_at timestamptz)")
    con.execute(
        "insert into agg_transform_runs values ('success', '2026-11-23 12:20:00+00'), "
        "('failed', '2026-11-23 14:20:00+00'), ('success', null)"
    )
    con.execute("create table stg_manifests (scheduled_at timestamptz)")
    # 60 minutes ending 14:59 with 52 distinct minutes (two captures per minute, 8 missing)
    con.execute(
        "insert into stg_manifests select m + interval 0 second from "
        "(select unnest(generate_series(timestamptz '2026-11-23 14:00:00+00', "
        "timestamptz '2026-11-23 14:59:00+00', interval 1 minute)) as m) "
        "where minute(m) not in (3, 4, 5, 6, 7, 8, 9, 10)"
    )
    con.execute("insert into stg_manifests select scheduled_at from stg_manifests")
    con.execute("create table int_scheduled_stop_times (scheduled_arrival_ts timestamptz)")
    con.execute(
        "create table int_usable_arrivals "
        "(scheduled_arrival_ts timestamptz, is_nearest_segment boolean)"
    )
    # the last complete local hour at 15:20Z is 13:00Z-14:00Z (08:00-09:00 local)
    con.execute(
        "insert into int_scheduled_stop_times select timestamptz '2026-11-23 13:30:00+00' "
        "from range(10)"
    )
    con.execute(
        "insert into int_usable_arrivals select timestamptz '2026-11-23 13:30:00+00', true "
        "from range(8)"
    )
    con.close()
    return path


def test_line_is_embedded_metric_format() -> None:
    line = emf.emf_line(NOW, {"GoldAgeMinutes": 62.5}, {"GoldAgeMinutes": "None"})
    doc = json.loads(line)
    assert doc["GoldAgeMinutes"] == 62.5
    assert doc["_aws"]["Timestamp"] == int(NOW.timestamp() * 1000)
    assert doc["_aws"]["CloudWatchMetrics"] == [
        {
            "Namespace": "Headway",
            "Dimensions": [[]],
            "Metrics": [{"Name": "GoldAgeMinutes", "Unit": "None"}],
        }
    ]


def test_gold_age_is_the_gap_between_exports(db: str) -> None:
    previous = emf.previous_export(db)
    assert previous == datetime(2026, 11, 23, 12, 20, tzinfo=UTC)  # failed runs do not count
    assert emf.gold_age_minutes(previous, NOW) == 180.0
    assert emf.gold_age_minutes(None, NOW) is None


def test_missed_runs_follow_the_newest_curated_minute(db: str) -> None:
    assert emf.missed_runs_last_hour(db) == 8


def test_arrival_coverage_of_the_last_complete_local_hour(db: str) -> None:
    assert emf.arrival_coverage_pct(db, NOW, LAG, "America/New_York") == 80.0


def test_arrival_coverage_is_skipped_outside_service_hours(db: str) -> None:
    night = datetime(2026, 11, 23, 7, 20, tzinfo=UTC)  # 02:20 local: the hour measured is 01:00
    assert emf.arrival_coverage_pct(db, night, LAG, "America/New_York") is None


def test_arrival_coverage_is_off_without_r2(db: str) -> None:
    kwargs: dict[str, Any] = {
        "previous": datetime(2026, 11, 23, 13, 20, tzinfo=UTC), "exported_at": NOW,
        "lag": LAG, "timezone": "America/New_York",
    }  # fmt: skip
    base = emf.collect(db, NOW, r2=False, **kwargs)
    assert base == {"GoldAgeMinutes": 120.0, "MissedRunsLastHour": 8}
    assert emf.collect(db, NOW, r2=True, **kwargs)["ArrivalCoveragePct"] == 80.0


def test_one_failing_metric_does_not_hide_the_others(tmp_path: Path) -> None:
    path = str(tmp_path / "empty.duckdb")
    duckdb.connect(path).close()
    got = emf.collect(
        path, NOW, previous=NOW - timedelta(minutes=60), exported_at=NOW,
        r2=True, lag=LAG, timezone="America/New_York",
    )  # fmt: skip
    assert got == {"GoldAgeMinutes": 60.0}


def test_emit_prints_one_bare_json_line_per_metric(capsys: pytest.CaptureFixture[str]) -> None:
    emf.emit(NOW, {"GoldAgeMinutes": 61.0, "ArrivalCoveragePct": 90.0})
    lines = capsys.readouterr().out.splitlines()
    assert [next(k for k in json.loads(x) if k != "_aws") for x in lines] == [
        "GoldAgeMinutes", "ArrivalCoveragePct",
    ]  # fmt: skip
    assert all(x.startswith("{") for x in lines)


def test_service_hours() -> None:
    assert [h for h in range(24) if not emf.in_service_hours(h)] == [1, 2, 3, 4]
    assert handler.SLO_LAG == LAG


def test_duration_is_one_seconds_metric_line() -> None:
    from datetime import UTC, datetime

    line = json.loads(emf.emit_duration(datetime(2026, 11, 26, 12, 0, tzinfo=UTC), 453.04))
    metric = line["_aws"]["CloudWatchMetrics"][0]
    assert metric["Namespace"] == "Headway"
    assert metric["Metrics"] == [{"Name": "TransformDurationSeconds", "Unit": "Seconds"}]
    assert line["TransformDurationSeconds"] == 453.0
