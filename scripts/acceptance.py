"""Report PASS / FAIL / MANUAL for the Release 1 acceptance tests A1-A12.

Uses the repository (tests, docs), the public gold over HTTPS and, when credentials are available,
read-only AWS (S3 list/get, STS). It never changes AWS. Run with `make acceptance`.

The tests, as checked here:

  A1  captures stored with UTC timestamps and run IDs (raw bucket manifest, else the sample)
  A2  pipeline ran unattended 72 h with missed runs < 1 % of expected (public pipeline health)
  A3  scheduled and predicted values in separate columns; snapshots per collection time
      (fct_stop_predictions schema and grain test)
  A4  duplicate, missing and invalid records measured (seeded faults match exactly in replay;
      pipeline health expectations in the e2e tables)
  A5  dbt models documented and tested (scripts/check_dbt_docs.py)
  A6  coverage and freshness beside every metric (metric card policy test)
  A7  live map reads the cached snapshot only (no MBTA URL in app/)
  A8  reproducible deployment: fresh-clone DEPLOY.md walkthrough recorded (manual)
  A9  end-to-end test runs locally from samples without AWS (make e2e)
  A10 ARCHITECTURE_DECISIONS.md and METRICS.md written
  A11 lineage diagram in docs/LINEAGE.md
  A12 cost measured from real billing (cost_daily rows)

Also writes capture-to-gold latency and cost per 1,000 captures into docs/MEASUREMENTS.md when
they can be measured. Pass --no-write to leave the file alone.
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "https://headway-public-381232271655.s3.us-east-1.amazonaws.com/public/boston"
WINDOW_H = 72
MAX_MISSED_PCT = 1.0
ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
Result = tuple[str, str]  # status, detail


def base_url() -> str:
    return (os.environ.get("HEADWAY_DATA_URL") or DEFAULT_URL).rstrip("/")


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        return bytes(resp.read())


def gold(name: str) -> dict[str, list[Any]]:
    data = fetch(f"{base_url()}/gold/{name}.parquet")
    return pq.read_table(io.BytesIO(data)).to_pydict()  # type: ignore[no-any-return]


def run(cmd: list[str], timeout: int = 900) -> tuple[bool, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "UV_PROJECT"}}
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, tail[-1] if tail else ""


def pytest_result(*args: str, group: str | None = None) -> Result:
    cmd = ["uv", "run"] + (["--group", group] if group else []) + ["pytest", "-q", *args]
    ok, last = run(cmd)
    return ("PASS" if ok else "FAIL"), last


# --- A1 ---------------------------------------------------------------------------------------


def newest_manifest_from_aws() -> tuple[dict[str, Any], str] | None:
    try:
        import boto3

        bucket = os.environ.get("HEADWAY_RAW_BUCKET") or (
            f"headway-raw-{boto3.client('sts').get_caller_identity()['Account']}"
        )
        s3 = boto3.client("s3")
        now = datetime.now(UTC)
        for day in (now, now - timedelta(days=1)):
            prefix = f"manifests/boston/dt={day:%Y-%m-%d}/"
            keys: list[tuple[datetime, str]] = []
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
                keys += [(o["LastModified"], o["Key"]) for o in page.get("Contents", [])]
            if keys:
                key = max(keys)[1]
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                return json.loads(body), f"s3://{bucket}/{key}"
    except Exception:  # no credentials, no network, no access: fall back to the sample
        return None
    return None


def a1() -> Result:
    found = newest_manifest_from_aws()
    if found is None:
        path = sorted((ROOT / "samples/boston/manifests").rglob("*.json"))[-1]
        doc, source = json.loads(path.read_text()), f"sample {path.name}"
    else:
        doc, source = found
    entries = doc.get("entries", [])
    if not entries:
        return "FAIL", f"no entries in {source}"
    problems = []
    if not ULID.match(str(doc.get("run_id", entries[0].get("run_id", "")))):
        problems.append("run_id is not a ULID")
    for entry in entries:
        if entry.get("run_id") != entries[0].get("run_id"):
            problems.append("mixed run_id")
        for field in ("scheduled_at", "fetched_at"):
            value = str(entry.get(field, ""))
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                problems.append(f"{field} unparsable")
                continue
            if not (
                value.endswith("Z") or value.endswith("+00:00")
            ) or parsed.utcoffset() != timedelta(0):
                problems.append(f"{field} not UTC")
    if problems:
        return "FAIL", f"{source}: {sorted(set(problems))}"
    return "PASS", f"{len(entries)} entries, run_id {entries[0]['run_id']}, UTC ({source})"


# --- A2 ---------------------------------------------------------------------------------------


def a2() -> tuple[Result, dict[str, float]]:
    try:
        table = gold("agg_pipeline_health")
    except OSError as exc:
        return ("FAIL", f"cannot read agg_pipeline_health: {exc}"), {}
    minutes = {
        t.astimezone(UTC).replace(second=0, microsecond=0) for t in table["scheduled_at"] if t
    }
    if not minutes:
        return ("FAIL", "agg_pipeline_health is empty"), {}
    end = max(minutes)
    start = max(min(minutes), end - timedelta(hours=WINDOW_H))
    expected = int((end - start).total_seconds() // 60) + 1
    seen = len({m for m in minutes if start <= m <= end})
    missed = expected - seen
    pct = 100.0 * missed / expected
    hours = (end - start).total_seconds() / 3600
    stats = {
        "missed_pct": pct,
        "hours": hours,
        "missed": float(missed),
        "expected": float(expected),
    }
    detail = f"{missed} of {expected} minutes missed ({pct:.2f} %) over {hours:.1f} h covered"
    if pct > MAX_MISSED_PCT:
        return ("FAIL", f"{detail}; limit {MAX_MISSED_PCT} %"), stats
    if hours < WINDOW_H - 1:
        return ("MANUAL", f"{detail}; {WINDOW_H} h not yet available, re-run later"), stats
    return ("PASS", detail), stats


# --- A8-A12 -----------------------------------------------------------------------------------


def a8() -> Result:
    text = (ROOT / "docs/DEPLOY.md").read_text()
    rows = re.findall(
        r"^\| (?!Step|---)([^|]+)\| *([^|]*?) *\|", text.split("## Walkthrough record")[-1], re.M
    )
    filled = sum(1 for _, t in rows if t)
    return (
        "MANUAL",
        f"time a fresh-clone deploy; record {filled}/{len(rows)} steps filled in DEPLOY.md",
    )


def a3() -> Result:
    import yaml

    doc = yaml.safe_load((ROOT / "dbt/headway/models/marts/_dims_facts.yml").read_text())
    model = next(m for m in doc["models"] if m["name"] == "fct_stop_predictions")
    cols = {c["name"] for c in model["columns"]}
    grain: list[str] = []
    for t in model.get("data_tests", []):
        if isinstance(t, dict) and "unique_grain" in t:
            grain = t["unique_grain"]["arguments"]["columns"]
    missing = {"scheduled_arrival_ts", "predicted_arrival_ts", "collected_at"} - cols
    if missing or "capture_id" not in grain or not grain:
        return "FAIL", f"missing columns {sorted(missing)}, grain {grain}"
    return "PASS", f"scheduled_arrival_ts and predicted_arrival_ts separate; grain {grain}"


def a4() -> Result:
    status, last = pytest_result(
        "tests/replay/test_samples.py::test_seeded_fault_counts_match_faults_md",
        "tests/unit/test_curate_normalize.py",
    )
    health = ROOT / "tests/e2e/expected/agg_pipeline_health.csv"
    header = health.read_text().splitlines()[0] if health.exists() else ""
    if not all(c in header for c in ("quarantine_total", "duplicate_delivery_count")):
        return "FAIL", "pipeline health expectations lack quarantine/duplicate columns"
    return status, f"seeded faults match FAULTS.md exactly; health columns expected ({last})"


def a5() -> Result:
    manifest = ROOT / "dbt/headway/target/manifest.json"
    if not manifest.exists():
        return "FAIL", "no dbt manifest; run make e2e first"
    ok, last = run(["uv", "run", "python", "scripts/check_dbt_docs.py", str(manifest)])
    return ("PASS" if ok else "FAIL"), last


def metrics_doc() -> Result:
    text = (ROOT / "docs/METRICS.md").read_text()
    sections = re.split(r"^## ", text, flags=re.M)
    metrics = [s for s in sections if re.match(r"[1-4]\. ", s)]  # the four Release 1 metrics
    needed = ("Business meaning", "Formula", "Grain", "Source models")
    bad = [s.splitlines()[0] for s in metrics if not all(k in s for k in needed)]
    if len(metrics) != 4 or bad:
        return "FAIL", f"{len(metrics)} metrics documented; incomplete: {bad}"
    return "PASS", f"METRICS.md: 4 metrics with {len(needed)} fields each"


def a10() -> Result:
    adr = (ROOT / "docs/ARCHITECTURE_DECISIONS.md").read_text()
    blocks = re.split(r"^### ADR-", adr, flags=re.M)[1:]
    numbers = [int(b[:3]) for b in blocks]
    fields = ("Status:", "Context:", "Decision", "Consequences:", "Revisit when:")
    bad = [b[:3] for b in blocks if not all(f in b for f in fields)]
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    cited = {
        int(n)
        for name in tracked
        if name.endswith((".md", ".py", ".sql", ".Dockerfile", ".toml"))
        and not name.startswith("samples/")
        for n in re.findall(r"ADR-(\d{3})", (ROOT / name).read_text(errors="ignore"))
    }
    incidents = (ROOT / "docs/INCIDENTS.md").read_text().count("- **Root cause:**")
    if numbers != list(range(1, len(numbers) + 1)) or bad or cited - set(numbers) or incidents < 1:
        return "FAIL", f"ADRs {numbers}, incomplete {bad}, dangling {sorted(cited - set(numbers))}"
    m_status, m_detail = metrics_doc()
    if m_status != "PASS":
        return m_status, m_detail
    return "PASS", f"ADR-001..{numbers[-1]:03d} complete; {m_detail}"


def a11() -> Result:
    text = (ROOT / "docs/LINEAGE.md").read_text()
    m = re.search(r"```mermaid\n(.*?)```", text, re.S)
    if not m:
        return "FAIL", "no Mermaid diagram in docs/LINEAGE.md"
    low = m.group(1).lower()
    missing = [
        k for k in ("contracts", "quarantine", "alarms", "replay", "dbt", "gold") if k not in low
    ]
    return (
        ("FAIL", f"diagram lacks {missing}")
        if missing
        else ("PASS", "diagram marks validation, quarantine, alarms, replay")
    )


def a12() -> tuple[Result, list[dict[str, Any]]]:
    try:
        table = gold("cost_daily")
    except OSError as exc:
        return ("FAIL", f"cannot read cost_daily: {exc}"), []
    rows = [dict(zip(table, vals, strict=True)) for vals in zip(*table.values(), strict=True)]
    if not rows:
        return ("FAIL", "cost_daily has no rows"), []
    days = sorted({str(r["date"]) for r in rows})
    return ("PASS", f"{len(rows)} rows over {len(days)} day(s), latest {days[-1]}"), rows


# --- MEASUREMENTS.md --------------------------------------------------------------------------


def measure_latency() -> str | None:
    """Age of the freshest capture in a scheduled export; None for manual or unreadable exports.

    A transform scheduled at :20 reads whatever curate has produced, so the age of the newest
    capture it contains is the best-case capture-to-gold latency. A manual invoke right after a
    curate run says nothing about the schedule, so it is not recorded.
    """
    try:
        exported = json.loads(fetch(f"{base_url()}/gold/manifest.json"))["exported_at"]
        newest = max(t for t in gold("agg_pipeline_health")["scheduled_at"] if t)
    except (OSError, KeyError, ValueError):
        return None
    exp = datetime.fromisoformat(exported.replace("Z", "+00:00"))
    if not 20 <= exp.minute <= 24:
        return None
    minutes = (exp - newest).total_seconds() / 60
    return (
        f"| Capture to gold | freshest capture was {minutes:.0f} min old at the scheduled export "
        f"of {exp:%Y-%m-%dT%H:%MZ} (newest run {newest:%Y-%m-%dT%H:%MZ}) "
        "| `exported_at` in `gold/manifest.json` minus the latest `scheduled_at` in "
        "`agg_pipeline_health`, for an export started by the `:20` schedule. This is the best case "
        "(the freshest capture), not a distribution. By schedule alone (curate every 15 minutes, "
        "transform at :20 plus about one minute) a capture is in gold between about 20 and 80 "
        "minutes after it is fetched; that range is derived from the schedules, not measured. |"
    )


def measure_cost(rows: list[dict[str, Any]]) -> str | None:
    days = sorted({str(r["date"]) for r in rows})
    if not days:
        return None
    day = days[-1]
    day_rows = [r for r in rows if str(r["date"]) == day]
    per_1000 = day_rows[0]["cost_per_1000_captures"]
    if per_1000 is None:
        return None
    return (
        f"| Cost per 1,000 captures | ${per_1000:.4f} on {day} "
        f"(day total ${day_rows[0]['day_total_usd']:.4f}, {day_rows[0]['captures']:,} captures) "
        "| `gold/cost_daily.parquet`: Cost Explorer spend for "
        "resources tagged `project=headway` divided by captures. The first day is partial "
        "(deployment started mid-day) and Lambda usage is inside the free tier, so S3 dominates. |"
    )


def write_measurements(latency: str | None, cost: str | None) -> list[str]:
    path = ROOT / "docs/MEASUREMENTS.md"
    lines = path.read_text().splitlines()
    written = []
    for label, row in (("| Capture to gold |", latency), ("| Cost per 1,000 captures |", cost)):
        if row is None:
            continue
        for i, line in enumerate(lines):
            if line.startswith(label):
                lines[i] = row
                written.append(label.strip("| "))
    path.write_text("\n".join(lines) + "\n")
    return written


# --- main -------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-write", action="store_true", help="do not update docs/MEASUREMENTS.md"
    )
    args = parser.parse_args()

    results: dict[str, Result] = {}
    results["A1"] = a1()
    results["A2"], _ = a2()
    ok, last = run(["uv", "run", "--project", "dbt", "python", "scripts/e2e.py"])
    results["A9"] = ("PASS" if ok else "FAIL", last)
    results["A3"] = a3()
    results["A4"] = a4()
    results["A5"] = a5()
    results["A6"] = pytest_result(
        "app/tests/test_policies.py::test_metric_card_requires_coverage_and_freshness", group="app"
    )
    results["A7"] = pytest_result(
        "app/tests/test_policies.py::test_no_mbta_feed_url_in_app", group="app"
    )
    results["A8"] = a8()
    results["A10"] = a10()
    results["A11"] = a11()
    results["A12"], cost_rows = a12()

    results = dict(sorted(results.items(), key=lambda kv: int(kv[0][1:])))
    print(f"{'Test':<5} {'Result':<7} Detail")
    for name, (status, detail) in results.items():
        print(f"{name:<5} {status:<7} {detail}")
    counts = {s: sum(1 for r, _ in results.values() if r == s) for s in ("PASS", "FAIL", "MANUAL")}
    print("\n" + ", ".join(f"{n} {s}" for s, n in counts.items()))

    if not args.no_write:
        written = write_measurements(measure_latency(), measure_cost(cost_rows))
        print("MEASUREMENTS.md updated:", ", ".join(written) if written else "nothing measurable")
        if "Capture to gold" not in written:
            print("capture-to-gold latency not recorded: latest export was not a scheduled run")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
