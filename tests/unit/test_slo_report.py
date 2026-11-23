"""The SLO report renders from the published tables and says so when there are none."""

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("slo_report", ROOT / "scripts" / "slo_report.py")
assert spec is not None and spec.loader is not None
slo_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(slo_report)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _daily(name: str, day: int, good: int, total: int, target: float = 99.0) -> dict[str, object]:
    pct = 100.0 * good / total
    return {"slo_name": name, "service_date": date(2026, 11, day), "target_pct": target,
            "good_events": good, "total_events": total, "actual_pct": pct, "met": pct >= target,
            "error_budget_used": (total - good) * 100.0 / ((100 - target) * total),
            "counts_toward_slo": True}  # fmt: skip


def test_report_lists_windows_and_days_and_marks_the_partial_day() -> None:
    daily = [
        _daily("capture_completeness", 22, 1430, 1440),
        _daily("capture_completeness", 23, 700, 720),
        _daily("arrival_coverage_disrupted", 23, 5, 10, target=80.0),
    ]
    rollup = [
        {"slo_name": "capture_completeness", "window_label": "last_7d", "target_pct": 99.0,
         "actual_pct": 98.8, "met": False, "error_budget_used": 1.2, "days_covered": 2,
         "good_events": 2130, "total_events": 2160, "counts_toward_slo": True},
    ]  # fmt: skip
    text = slo_report.render_report(
        daily, rollup, "https://x/public/boston", NOW, "2026-11-24T10:00:00Z"
    )
    assert "| Capture completeness | last 7d | 99.00% | 98.80% | **missed** | 120% | 2 |" in text
    assert (
        "| 2026-11-23 (partial) | Capture completeness | 97.22% | **missed** | 700 / 720 |" in text
    )
    assert "| 2026-11-22 | Capture completeness | 99.31% | met | 1,430 / 1,440 |" in text
    assert "under a service disruption (separate)" in text  # listed, not hidden
    assert "metrics as of 2026-11-24T10:00:00Z" in text


def test_report_without_tables_says_release_2_is_off() -> None:
    text = slo_report.render_report(None, None, "https://x/public/boston", NOW)
    assert "Release 2 is not enabled" in text and "Nothing to report" in text


def test_report_reads_an_export_over_a_file_url(tmp_path: Path) -> None:
    gold = tmp_path / "gold"
    gold.mkdir()
    con = duckdb.connect()
    con.execute(
        "copy (select 'capture_completeness' as slo_name, date '2026-11-23' as service_date, "
        "99.0 as target_pct, 99::bigint as good_events, 100::bigint as total_events, "
        "99.0 as actual_pct, "
        f"true as met, 100.0 as error_budget_used) to '{gold}/agg_slo_daily.parquet'"
    )
    con.execute(
        "copy (select 'capture_completeness' as slo_name, 'last_7d' as window_label, "
        "99.0 as target_pct, 99.0 as actual_pct, true as met, 100.0 as error_budget_used, "
        "1 as days_covered) "
        f"to '{gold}/agg_slo_rollup.parquet'"
    )
    (gold / "manifest.json").write_text('{"metrics_as_of": "2026-11-24T10:00:00Z"}')
    out = tmp_path / "SLO_REPORT.md"
    assert slo_report.main(["--url", f"file://{tmp_path}", "--out", str(out)]) == 0
    assert "| Capture completeness | last 7d |" in out.read_text()
    empty = tmp_path / "none"
    assert slo_report.load_table(f"file://{empty}", "agg_slo_daily") is None
