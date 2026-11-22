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
