import json
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from lib import data
from lib.coverage import coverage_badge
from lib.freshness import format_age, is_snapshot_stale, seconds_since
from lib.ranking import rank_lines
from lib.timefmt import date_label, format_local, hour_start_utc, local_date, to_local, weekday_name


@pytest.mark.parametrize(
    ("pct", "level", "rankable"),
    [
        (100, "normal", True),
        (80, "normal", True),
        (79.9, "low", True),
        (50, "low", True),
        (49.9, "insufficient", False),
        (None, "insufficient", False),
        (float("nan"), "insufficient", False),
    ],
)
def test_coverage_badge(pct: float | None, level: str, rankable: bool) -> None:
    badge = coverage_badge(pct)
    assert (badge.level, badge.rankable) == (level, rankable)


def test_badge_labels() -> None:
    assert coverage_badge(60).label == "low coverage"
    assert coverage_badge(10).label == "insufficient data"


def test_ranking_within_spread_ranks_together() -> None:
    r = rank_lines([("Red", 90.0, 95.0), ("Blue", 30.0, 85.0), ("Orange", 60.0, 80.0)])
    assert [(x.line, x.rank, x.group) for x in r.lines] == [
        ("Blue", 1, 0),
        ("Orange", 2, 0),
        ("Red", 3, 0),
    ]
    assert r.notes == []


def test_ranking_states_the_gap_instead_of_ranking_across_it() -> None:
    r = rank_lines([("Red", 10.0, 95.0), ("Blue", 5.0, 70.0), ("Orange", 8.0, 90.0)])
    by_line = {x.line: x for x in r.lines}
    assert by_line["Red"].group == by_line["Orange"].group == 0
    assert by_line["Blue"].group == 1 and by_line["Blue"].rank == 1
    assert len(r.notes) == 1 and "Blue" in r.notes[0] and "20 points" in r.notes[0]


def test_ranking_boundary_is_inclusive_at_15() -> None:
    r = rank_lines([("A", 1.0, 95.0), ("B", 2.0, 80.0)])
    assert {x.group for x in r.lines} == {0}


def test_ranking_excludes_insufficient_data() -> None:
    r = rank_lines([("Red", 10.0, 90.0), ("Blue", 1.0, 30.0), ("Mattapan", None, None)])
    ranked = {x.line: x.rank for x in r.lines}
    assert ranked == {"Red": 1, "Blue": None, "Mattapan": None}
    assert any("Blue" in n and "Mattapan" in n for n in r.notes)


def test_to_local_handles_dst() -> None:
    assert to_local("2026-07-01T12:00:00Z").hour == 8
    assert to_local("2026-12-01T12:00:00Z").hour == 7
    assert format_local("2026-11-22T14:30:00Z") == "2026-11-22 09:30 EST"


def test_hour_start_and_dates() -> None:
    assert hour_start_utc(date(2026, 11, 22), 9) == datetime(2026, 11, 22, 14, tzinfo=UTC)
    # 01:00 local belongs to the previous service date
    assert local_date(date(2026, 11, 21), 1) == date(2026, 11, 22)
    assert hour_start_utc(date(2026, 11, 21), 1) == datetime(2026, 11, 22, 6, tzinfo=UTC)
    assert weekday_name(date(2026, 11, 22), 9) == "Sun"
    assert date_label(date(2026, 3, 8)) == "2026-03-08"


def test_freshness() -> None:
    now = datetime(2026, 11, 22, 15, 0, 0, tzinfo=UTC)
    assert seconds_since("2026-11-22T14:58:30Z", now) == 90
    assert seconds_since("2026-11-22T15:05:00Z", now) == 0
    assert not is_snapshot_stale(180)
    assert is_snapshot_stale(181)
    assert format_age(45) == "45s ago"
    assert format_age(125) == "2 min ago"
    assert format_age(7500) == "2 h 5 min ago"


def test_data_url_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEADWAY_DATA_URL", raising=False)
    with pytest.raises(data.DataUnavailableError):
        data.base_url()
    with pytest.raises(data.DataUnavailableError):
        data.base_url("http://insecure.example/x")
    assert data.base_url("https://example.org/boston/") == "https://example.org/boston"
    monkeypatch.setenv("HEADWAY_DATA_URL", "file:///tmp/x")
    assert data.base_url() == "file:///tmp/x"


def test_load_from_file_url(tmp_path: Path) -> None:
    (tmp_path / "gold").mkdir()
    (tmp_path / "live").mkdir()
    pd.DataFrame({"stop_id": ["a"], "stop_name": ["A"]}).to_parquet(
        tmp_path / "gold/dim_stop.parquet"
    )
    (tmp_path / "gold/manifest.json").write_text(
        json.dumps({"exported_at": "2026-01-01T00:00:00Z"})
    )
    (tmp_path / "live/vehicles_latest.json").write_text(json.dumps({"vehicles": []}))
    base = f"file://{tmp_path}"
    assert data.load_table(base, "dim_stop")["stop_name"].tolist() == ["A"]
    assert data.load_manifest(base)["exported_at"].startswith("2026")
    assert data.load_snapshot(base) == {"vehicles": []}
    with pytest.raises(ValueError):
        data.load_table(base, "secrets")
    with pytest.raises(data.DataUnavailableError):
        data.load_table(base, "dim_route")
