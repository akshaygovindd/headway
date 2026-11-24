"""scripts/export_alerts_for_labelling.py: dedupe, effect spread, empty label column."""

import csv
import importlib.util
import io
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from headway.storage import LocalStorage

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_alerts_for_labelling.py"
spec = importlib.util.spec_from_file_location("export_alerts", SCRIPT)
assert spec and spec.loader
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def _storage(tmp_path: Path, rows: list[tuple[str, str, str, str]]) -> LocalStorage:
    table = pa.table(
        {
            "alert_id": [r[0] for r in rows],
            "effect": [r[1] for r in rows],
            "cause": ["MAINTENANCE"] * len(rows),
            "route_id": [r[2] for r in rows],
            "header_text": [r[3] for r in rows],
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    st = LocalStorage(tmp_path)
    st.put_bytes("curated/boston/table=alerts/dt=2026-11-22/hour=10/p.parquet", buf.getvalue())
    return st


ROWS = [("1", "DETOUR", "Red", "Shuttle buses replace trains")] * 3 + [
    ("1", "DETOUR", "Orange", "Shuttle buses replace trains"),
    ("2", "ACCESSIBILITY_ISSUE", "Red", "Elevator out"),
    ("3", "ACCESSIBILITY_ISSUE", "Blue", "Escalator out"),
    ("4", "ACCESSIBILITY_ISSUE", "Blue", ""),
]


def test_dedupe_by_id_and_header_and_merge_routes(tmp_path: Path) -> None:
    alerts = export.read_alerts(_storage(tmp_path, ROWS), "curated/boston/table=alerts/")
    assert len(alerts) == 3  # blank header dropped, repeats merged
    detour = next(a for a in alerts if a["alert_id"] == "1")
    assert detour["routes"] == {"Red", "Orange"}


def test_sample_spreads_effects_and_is_seeded(tmp_path: Path) -> None:
    alerts = export.read_alerts(_storage(tmp_path, ROWS), "curated/boston/table=alerts/")
    picked = export.sample(alerts, 2, seed=1)
    assert {a["effect"] for a in picked} == {"DETOUR", "ACCESSIBILITY_ISSUE"}
    assert export.sample(alerts, 2, seed=1) == picked


def test_cli_writes_empty_labels_and_protects_labelled_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _storage(tmp_path, ROWS)
    out = tmp_path / "labels.csv"
    args = ["--local", str(tmp_path), "--out", str(out)]
    assert export.main(args) == 0
    with out.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert all(r["label"] == "" for r in rows)
    assert list(rows[0]) == export.COLUMNS

    rows[0]["label"] = "delay"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=export.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    assert export.main(args) == 1
    assert "already has labels" in capsys.readouterr().err
    assert export.main([*args, "--force"]) == 0
