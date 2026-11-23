"""Write docs/SLO_REPORT.md from the published SLO tables.

Usage: python scripts/slo_report.py [--url BASE] [--out docs/SLO_REPORT.md] [--days 14]

Reads gold/agg_slo_daily.parquet, gold/agg_slo_rollup.parquet and gold/manifest.json over HTTPS
from BASE (default HEADWAY_DATA_URL, else the public bucket). No credentials are used or needed.
A deployment that has not enabled Release 2 has no SLO tables; the report then says so.
"""

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

DEFAULT_URL = "https://headway-public-381232271655.s3.us-east-1.amazonaws.com/public/boston"
ORDER = (
    "capture_completeness",
    "gold_freshness",
    "transform_success",
    "arrival_coverage",
    "arrival_coverage_disrupted",
)
LABELS = {
    "capture_completeness": "Capture completeness",
    "gold_freshness": "Gold freshness (at most 90 min old)",
    "transform_success": "Transform success",
    "arrival_coverage": "Arrival coverage",
    "arrival_coverage_disrupted": "Arrival coverage under a service disruption (separate)",
}
Row = dict[str, Any]


def base_url(value: str | None = None) -> str:
    url = value or os.environ.get("HEADWAY_DATA_URL") or DEFAULT_URL
    if not url.startswith(("https://", "file://")):
        raise SystemExit("the data URL must start with https:// (or file:// for a local export)")
    return url.rstrip("/")


def fetch(url: str) -> bytes | None:
    """The object's bytes, or None when it does not exist (HTTP 403/404 or a missing file)."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            return bytes(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise
    except (FileNotFoundError, urllib.error.URLError) as exc:
        if isinstance(exc, FileNotFoundError) or isinstance(
            getattr(exc, "reason", None), FileNotFoundError
        ):
            return None
        raise


def load_table(base: str, name: str) -> list[Row] | None:
    body = fetch(f"{base}/gold/{name}.parquet")
    return None if body is None else pq.read_table(io.BytesIO(body)).to_pylist()


def load_manifest(base: str) -> Row:
    body = fetch(f"{base}/gold/manifest.json")
    return json.loads(body) if body else {}


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


def _met(value: bool | None) -> str:
    return "n/a" if value is None else ("met" if value else "**missed**")


def _budget(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.0f}%"


def _key(row: Row) -> int:
    name = row["slo_name"]
    return ORDER.index(name) if name in ORDER else len(ORDER)


def render_report(
    daily: list[Row] | None,
    rollup: list[Row] | None,
    source: str,
    generated_at: datetime,
    metrics_as_of: str | None = None,
    days: int = 14,
) -> str:
    head = [
        "# SLO report",
        "",
        f"Generated {generated_at:%Y-%m-%d %H:%M} UTC from `{source}/gold`"
        + (f"; metrics as of {metrics_as_of}." if metrics_as_of else "."),
        "Definitions and targets are in [SLO.md](SLO.md).",
        "",
    ]
    if not daily or rollup is None:
        return "\n".join(
            [
                *head,
                "No SLO tables are published at this source: Release 2 is not enabled in the "
                "deployment (`agg_slo_daily` and `agg_slo_rollup` are not in its gold export), "
                "or no day has been measured yet. Nothing to report.",
                "",
            ]
        )
    lines = [*head, "## Last 7 and 30 days", ""]
    lines += [
        "| Objective | Window | Target | Actual | Result | Error budget used | Days measured |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(rollup, key=lambda r: (_key(r), r["window_label"])):
        lines.append(
            f"| {LABELS.get(r['slo_name'], r['slo_name'])} | {r['window_label'].replace('_', ' ')} "
            f"| {_pct(r['target_pct'])} | {_pct(r['actual_pct'])} | {_met(r['met'])} "
            f"| {_budget(r['error_budget_used'])} | {r['days_covered']} |"
        )
    latest: date = max(r["service_date"] for r in daily)
    recent = sorted({r["service_date"] for r in daily}, reverse=True)[:days]
    lines += ["", f"## Daily, last {len(recent)} service dates", ""]
    lines += [
        "| Service date | Objective | Actual | Result | Good / total |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in sorted(
        (r for r in daily if r["service_date"] in recent),
        key=lambda r: (-r["service_date"].toordinal(), _key(r)),
    ):
        partial = " (partial)" if r["service_date"] == latest else ""
        lines.append(
            f"| {r['service_date']}{partial} | {LABELS.get(r['slo_name'], r['slo_name'])} "
            f"| {_pct(r['actual_pct'])} | {_met(r['met'])} "
            f"| {int(r['good_events']):,} / {int(r['total_events']):,} |"
        )
    lines += [
        "",
        "The latest service date is still in progress and counts events up to the last complete "
        "half hour. The disrupted-lines row does not count toward the arrival coverage objective.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url")
    parser.add_argument("--out", type=Path, default=Path("docs/SLO_REPORT.md"))
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args(argv)
    base = base_url(args.url)
    report = render_report(
        load_table(base, "agg_slo_daily"),
        load_table(base, "agg_slo_rollup"),
        base,
        datetime.now(UTC),
        load_manifest(base).get("metrics_as_of"),
        args.days,
    )
    args.out.write_text(report)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
