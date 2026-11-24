"""scripts/alert_handcheck.py: at least ten rows of each kind, empty reviewer columns."""

import csv
import importlib.util
from pathlib import Path

import duckdb
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "alert_handcheck.py"
spec = importlib.util.spec_from_file_location("alert_handcheck", SCRIPT)
assert spec and spec.loader
handcheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handcheck)


def _db(path: Path, n: int = 14) -> Path:
    con = duckdb.connect(str(path))
    con.execute(
        "create table int_alert_matching (kind varchar, item_id varchar, line varchar, "
        "event_ts timestamptz, service_date date, n_counterparts bigint, "
        "match_status varchar, alert_lag_s double, first_alert_id varchar, "
        "counterpart_ids varchar)"
    )
    con.execute("create table fct_incidents (incident_id varchar, incident_start_ts timestamptz, "
                "incident_end_ts timestamptz, thresholds_crossed varchar)")  # fmt: skip
    con.execute("create table agg_incidents (incident_id varchar, summary_text varchar)")
    con.execute("create table int_alert_scopes (line varchar, alert_id varchar, effect varchar, "
                "header_text varchar, scope varchar, is_service_disruption boolean, "
                "first_seen timestamptz, last_seen timestamptz)")  # fmt: skip
    for i in range(n):
        for status in ("matched", "unmatched_incident"):
            iid = f"{status}-i{i}"
            con.execute(
                f"insert into int_alert_matching values ('incident', '{iid}', 'Red', "
                f"'2026-11-22 14:00:00+00', '2026-11-22', 1, '{status}', 60, 'a', 'a{i}')"
            )
            con.execute(f"insert into fct_incidents values ('{iid}', '2026-11-22 14:00:00+00', "
                        "'2026-11-22 16:00:00+00', 'gap')")  # fmt: skip
        for status in ("matched", "unmatched_alert"):
            aid = f"{status}-a{i}"
            con.execute(
                f"insert into int_alert_matching values ('alert', '{aid}', 'Blue', "
                f"'2026-11-22 14:00:00+00', '2026-11-22', 0, '{status}', null, null, '')"
            )
            con.execute(
                f"insert into int_alert_scopes values ('Blue', '{aid}', 'DETOUR', 'Shuttle', "
                "'line', true, '2026-11-22 14:00:00+00', '2026-11-22 15:00:00+00')"
            )
    con.close()
    return path


def test_sample_has_every_kind_and_blank_reviewer_columns(tmp_path: Path) -> None:
    db = _db(tmp_path / "m.duckdb")
    out = tmp_path / "out.csv"
    assert handcheck.main(["--db", str(db), "--out", str(out), "--per-kind", "10"]) == 0
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 40
    for kind, status in handcheck.KINDS:
        assert sum(r["kind"] == kind and r["match_status"] == status for r in rows) == 10
    assert all(r["reviewer_verdict"] == "" and r["reviewer_note"] == "" for r in rows)
    assert list(rows[0]) == handcheck.COLUMNS


def test_sample_is_deterministic_and_reports_shortfalls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _db(tmp_path / "m.duckdb", n=4)
    one, found = handcheck.sample(db, 10, "s")
    two, _ = handcheck.sample(db, 10, "s")
    assert one == two and set(found.values()) == {4}
    handcheck.main(["--db", str(db), "--out", str(tmp_path / "o.csv")])
    assert "fewer than 10 available" in capsys.readouterr().out
