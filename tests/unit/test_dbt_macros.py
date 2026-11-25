"""Cross-check the dbt time macros against headway.timeutil.

The macros are rendered and executed by dbt itself (`dbt show --inline`), so the
real macro source is what gets tested.
"""

import json
import os
import shutil
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from headway.config import load_city_config
from headway.contracts.manifest import ManifestEntry
from headway.curate.meta import write_manifest_parts
from headway.storage import LocalStorage
from headway.timeutil import gtfs_time_to_utc, service_date_for

ROOT = Path(__file__).resolve().parents[2]
TZ = "America/New_York"

GTFS_CASES = [
    (date(2026, 3, 8), "00:00:00"),
    (date(2026, 3, 8), "01:59:59"),
    (date(2026, 3, 8), "03:00:00"),
    (date(2026, 3, 8), "25:10:00"),
    (date(2026, 3, 7), "26:30:00"),
    (date(2026, 11, 1), "00:30:00"),
    (date(2026, 11, 1), "01:30:00"),
    (date(2026, 11, 1), "02:15:00"),
    (date(2026, 11, 1), "25:10:00"),
    (date(2026, 10, 31), "25:10:00"),
    (date(2026, 11, 22), "08:05:30"),
    (date(2026, 7, 4), "24:00:00"),
]

TS_CASES = [
    datetime(2026, 11, 22, 7, 59, 59, tzinfo=UTC),  # 02:59 local: previous service date
    datetime(2026, 11, 22, 8, 0, 0, tzinfo=UTC),  # 03:00 local
    datetime(2026, 11, 22, 14, 30, 0, tzinfo=UTC),
    datetime(2026, 11, 1, 5, 30, 0, tzinfo=UTC),  # 01:30 EDT, first pass
    datetime(2026, 11, 1, 6, 30, 0, tzinfo=UTC),  # 01:30 EST, second pass
    datetime(2026, 11, 1, 7, 59, 0, tzinfo=UTC),  # 02:59 EST
    datetime(2026, 3, 8, 6, 59, 0, tzinfo=UTC),  # 01:59 EST, before spring forward
    datetime(2026, 3, 8, 7, 0, 0, tzinfo=UTC),  # 03:00 EDT
    datetime(2026, 3, 8, 8, 0, 0, tzinfo=UTC),
]


def _dbt_show(sql: str, tmp_path: Path, vars_json: str = "{}") -> list[dict[str, str]]:
    if shutil.which("uv") is None:
        pytest.skip("uv is required to run dbt")
    env = {**os.environ, "HEADWAY_DUCKDB_PATH": str(tmp_path / "macros.duckdb")}
    proc = subprocess.run(
        [
            "uv", "run", "--project", "dbt", "dbt", "show", "--quiet",
            "--project-dir", "dbt/headway", "--profiles-dir", "dbt/headway",
            "--target", "local", "--output", "json", "--limit", "1000", "--vars", vars_json,
            "--inline", sql,
        ],  # fmt: skip
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)["show"]


def test_gtfs_time_to_utc_macro_matches_python(tmp_path: Path) -> None:
    selects = [
        f"select {i} as k, strftime(cast({{{{ gtfs_time_to_utc(\"date '{d}'\", \"'{t}'\") }}}}"
        f" as timestamp), '%Y-%m-%dT%H:%M:%S') as got"
        for i, (d, t) in enumerate(GTFS_CASES)
    ]
    rows = {int(r["k"]): r["got"] for r in _dbt_show(" union all ".join(selects), tmp_path)}
    for i, (d, t) in enumerate(GTFS_CASES):
        expected = gtfs_time_to_utc(d, t, TZ).strftime("%Y-%m-%dT%H:%M:%S")
        assert rows[i] == expected, (d, t)


def test_service_date_from_ts_macro_matches_python(tmp_path: Path) -> None:
    selects = []
    for i, ts in enumerate(TS_CASES):
        arg = f"timestamptz '{ts:%Y-%m-%d %H:%M:%S}+00'"
        macro = '{{ service_date_from_ts("' + arg + '") }}'
        selects.append(f"select {i} as k, strftime({macro}, '%Y-%m-%d') as got")
    rows = {int(r["k"]): r["got"] for r in _dbt_show(" union all ".join(selects), tmp_path)}
    for i, ts in enumerate(TS_CASES):
        assert rows[i] == service_date_for(ts, TZ, 3).isoformat(), ts


def test_lookback_filter_is_empty_without_the_var(tmp_path: Path) -> None:
    sql = "select '{{ lookback_filter() }}' as got"
    assert _dbt_show(sql, tmp_path)[0]["got"] == ""


def test_lookback_filter_bounds_the_dt_partition(tmp_path: Path) -> None:
    sql = 'select \'{{ lookback_filter() | replace("\'", "") }}\' as got'
    got = _dbt_show(sql, tmp_path, '{"lookback_days": 7}')[0]["got"]
    assert got == "where cast(dt as date) >= cast(now() at time zone UTC as date) - 7"


def test_manifest_timestamps_are_tz_aware_on_every_duckdb(tmp_path: Path) -> None:
    """The curated manifests Parquet must read back as TIMESTAMPTZ (UTC instants).

    DuckDB 1.5 auto-detects `...Z` JSON strings as naive TIMESTAMP while 2.0 detects TIMESTAMPTZ,
    which shifted the capture/schedule comparison by the session offset. The Parquet table has an
    explicit UTC timestamp schema, so every DuckDB version reads the same type and instants.
    """
    stamps = list(TS_CASES)  # includes both DST transition days
    entries = [
        ManifestEntry(
            run_id="r1", capture_id=f"c{i}", feed="trip_updates", city_id="boston", status="ok",
            http_status=200, scheduled_at=t, fetched_at=t.replace(microsecond=250000),
            feed_header_ts=t, entity_count=1, bytes=1, content_hash="h", s3_key="raw/x",
        )
        for i, t in enumerate(stamps)
    ]  # fmt: skip
    write_manifest_parts(LocalStorage(tmp_path), load_city_config("boston"), entries)
    sql = (
        "select any_value(typeof(fetched_at)) as typ, "
        "string_agg(strftime(fetched_at at time zone 'UTC', '%Y-%m-%dT%H:%M:%S.%g'), ',' "
        "order by capture_id) as fetched "
        "from {{ source('curated', 'manifests') }}"
    )
    (row,) = _dbt_show(sql, tmp_path, json.dumps({"curated_root": str(tmp_path)}))
    assert row["typ"] == "TIMESTAMP WITH TIME ZONE"
    expected = [f"{t:%Y-%m-%dT%H:%M:%S}.250" for t in stamps]
    assert sorted(row["fetched"].split(",")) == sorted(expected)


R4_TABLES = {
    "agg_headway_segment_window",
    "agg_marey_last_hour",
    "agg_capture_heartbeat_hourly",
    "agg_ett_band_window",
    "agg_service_disruptions",
}


def _allowlist_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> set[str]:
    for name in ("HEADWAY_ENABLE_R2", "HEADWAY_ENABLE_R3", "HEADWAY_ENABLE_R4"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    sql = "select '{{ gold_allowlist().keys() | list | join(',') }}' as keys"
    return set(_dbt_show(sql, tmp_path)[0]["keys"].split(","))


def test_release_4_allowlist_needs_r2_and_the_r4_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not R4_TABLES & _allowlist_keys(tmp_path, monkeypatch)
    assert not R4_TABLES & _allowlist_keys(tmp_path, monkeypatch, HEADWAY_ENABLE_R2="true")
    assert not R4_TABLES & _allowlist_keys(tmp_path, monkeypatch, HEADWAY_ENABLE_R4="true")
    on = _allowlist_keys(tmp_path, monkeypatch, HEADWAY_ENABLE_R2="true", HEADWAY_ENABLE_R4="true")
    assert on >= R4_TABLES and "agg_incidents" not in on  # independent of R3
