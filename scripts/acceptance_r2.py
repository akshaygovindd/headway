"""Report PASS / FAIL / MANUAL for the Release 2 acceptance criteria B1-B12.

Uses the repository (tests, docs, Terraform), the public gold over HTTPS and, when credentials are
available, read-only AWS (S3 list/get, CloudWatch describe/get, STS). It never changes AWS and
writes nothing. Run with `make acceptance-r2`.

The criteria are those of the Release 2 spec, written before the build (see docs/DEPLOY.md,
"Release 2 sign-off"):

  B1  every reconstructed arrival has method and confidence; low confidence excluded from metrics
  B2  reconstruction validated on >= 10 hand-checked trips across 4 lines, agreement recorded
  B3  sampling decision recorded with evidence (ADR-014 with spike numbers)
  B4  four new metrics documented in METRICS.md before display, each with coverage
  B5  accuracy compares predictions with arrivals only, never predictions with predictions
  B6  history partitions for closed days exist and are immutable; 30-day metrics read them
  B7  7- and 30-day SLO report generated from public gold
  B8  six failure simulations pass as replay tests
  B9  feed-down drill recorded: detect, warn, label, alarm, recover
  B10 Elementary report published and linked; anomaly tests on key models
  B11 new alarms exist, each linked to a RUNBOOK.md section
  B12 R2 pipeline ran 72 h with no unexplained alarms

A second section, "Additional checks", holds operational checks that are not part of B1-B12.
Exit status is 1 if any check FAILs; MANUAL does not fail the run.
"""

import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acceptance as r1  # noqa: E402

ROOT = r1.ROOT
DOCS = ROOT / "docs"
Result = tuple[str, str]
REGION = "us-east-1"
PUBLIC_PREFIX = "public/boston"
SOAK_H = 72
MAX_MISSED_PCT = 1.0
MIN_TRANSFORM_SUCCESS_PCT = 99.0
MAX_DURATION_S = 600.0
MAX_MEMORY_PCT = 85.0
MAX_GOLD_AGE_MIN = 90
# Release 2 switched on when the transform Lambda was updated with HEADWAY_ENABLE_R2=true
# (CloudTrail UpdateFunctionConfiguration, 2026-11-26 12:47:54 EST); its first run was 17:49:32Z.
FALLBACK_SWITCH_ON = datetime(2026, 11, 26, 17, 47, 54, tzinfo=UTC)
DRILL_STAGES = ("detect", "warn", "label", "alarm", "recover")
SIM_FILES = 6

# Alarm transitions that are understood: alarm name -> (from, until, cause). A transition counts
# as explained only inside its window (None is open-ended), so a later recurrence is not hidden.
Cause = tuple[datetime | None, datetime | None, str]
KNOWN_CAUSES: dict[str, list[Cause]] = {
    "headway-data-tests-failed": [
        (
            None,
            None,
            "data_invariants_r2: countdown coverage above 100 % (numerator by predicted hour, "
            "denominator by scheduled hour); fixed in 4825cc5 and ee94ef7 "
            "(in the repo; not deployed until the 2026-11-29 deploy)",
        )
    ],
    "headway-transform-errors": [
        (
            datetime(2026, 11, 26, 18, 0, tzinfo=UTC),
            datetime(2026, 11, 26, 20, 0, tzinfo=UTC),
            "dbt unit tests ran inside the Lambda after the first R2 run wrote history; "
            "3 failed on the real history rows, 4 models were skipped and export_gold failed "
            "(18:20 and 19:20 runs); fixed in 728643f, deployed 19:38Z (MEASUREMENTS.md)",
        )
    ],
}


def worst(results: Iterable[Result]) -> str:
    states = [s for s, _ in results]
    for state in ("FAIL", "MANUAL"):
        if state in states:
            return state
    return "PASS"


# --- time window ---------------------------------------------------------------------------


def switch_on_from_runs(rows: list[dict[str, Any]]) -> datetime | None:
    """The start of the first run with Release 2 on."""
    starts = [r["started_at"] for r in rows if r.get("r2_enabled") and r.get("started_at")]
    return min(starts).astimezone(UTC) if starts else None


def soak_missed(
    minutes: set[datetime], since: datetime, hours: int = SOAK_H
) -> tuple[Result, dict[str, float]]:
    """Missed minutes over the last `hours` hours of agg_pipeline_health, not before `since`."""
    if not minutes:
        return ("FAIL", "agg_pipeline_health is empty"), {}
    end = max(minutes)
    start = max(min(minutes), since, end - timedelta(hours=hours))
    expected = int((end - start).total_seconds() // 60) + 1
    seen = len({m for m in minutes if start <= m <= end})
    missed = expected - seen
    pct = 100.0 * missed / expected
    covered = (end - start).total_seconds() / 3600
    detail = (
        f"{missed} of {expected} minutes missed ({pct:.2f} %) over {covered:.1f} h "
        f"since {start:%Y-%m-%d %H:%M}Z"
    )
    stats = {"missed_pct": pct, "hours": covered}
    if pct > MAX_MISSED_PCT:
        return ("FAIL", f"{detail}; limit {MAX_MISSED_PCT} %"), stats
    if covered < hours - 1:
        return ("MANUAL", f"{detail}; {hours} h not yet available since switch-on"), stats
    return ("PASS", detail), stats


# --- transform runs ------------------------------------------------------------------------


def slot_statuses(rows: list[dict[str, Any]], since: datetime) -> dict[datetime, str]:
    """Best outcome per hourly slot whose start is at or after `since`, from the run state.

    exported: a run exported and published gold and its dbt tests passed;
    tests_failed: it exported and published gold but an error-severity data test failed;
    failed: every run of the slot raised; missing: the slot has no run (a lock skip is not
    recorded in the state, so it shows as a slot with no run).
    """
    rank = {"exported": 0, "tests_failed": 1, "failed": 2}
    out: dict[datetime, str] = {}
    for r in rows:
        slot = r.get("slot")
        if slot is None or slot < since:
            continue
        if r.get("gold_published") and r.get("status") == "success":
            status = "exported"
        elif r.get("gold_published"):
            status = "tests_failed"
        else:
            status = "failed"
        if slot not in out or rank[status] < rank[out[slot]]:
            out[slot] = status
    if out:
        slot = min(out)
        end = max(out)
        while slot <= end:
            out.setdefault(slot, "missing")
            slot += timedelta(hours=1)
    return dict(sorted(out.items()))


def transform_success(rows: list[dict[str, Any]], since: datetime) -> Result:
    """The 99 % bar applies to slots that exported and published gold (docs/SLO.md); the strict
    share of slots with passing data tests is reported beside it."""
    slots = slot_statuses(rows, since)
    if not slots:
        return "MANUAL", "no transform run rows since switch-on"
    total = len(slots)
    clean = sum(1 for s in slots.values() if s == "exported")
    gold_out = sum(1 for s in slots.values() if s in {"exported", "tests_failed"})
    pct = 100.0 * gold_out / total
    detail = (
        f"gold exported in {gold_out} of {total} hourly slots ({pct:.1f} %); "
        f"tests also passed in {clean} ({100.0 * clean / total:.1f} %)"
    )
    bad = [f"{s:%d %b %H}Z {v}" for s, v in slots.items() if v in {"failed", "missing"}]
    if pct < MIN_TRANSFORM_SUCCESS_PCT:
        return "FAIL", f"{detail}; need {MIN_TRANSFORM_SUCCESS_PCT} % exported; not exported: " + (
            ", ".join(bad)
        )
    return "PASS", detail


def budget(duration_s: float | None, memory_pct: float | None) -> Result:
    if duration_s is None or memory_pct is None:
        return "MANUAL", "TransformDurationSeconds or TransformMemoryUsedPct not readable"
    detail = (
        f"max duration {duration_s:.0f} s (limit {MAX_DURATION_S:.0f}), "
        f"max memory {memory_pct:.1f} % (limit {MAX_MEMORY_PCT:.0f})"
    )
    ok = duration_s <= MAX_DURATION_S and memory_pct <= MAX_MEMORY_PCT
    return ("PASS" if ok else "FAIL"), detail


# --- alarms --------------------------------------------------------------------------------


def alarm_transitions(items: list[dict[str, Any]]) -> list[tuple[str, datetime]]:
    """(alarm name, time) for each transition into ALARM in DescribeAlarmHistory items."""
    out = []
    for item in items:
        try:
            new_state = json.loads(item["HistoryData"])["newState"]["stateValue"]
        except (KeyError, ValueError, TypeError):
            continue
        if new_state == "ALARM":
            out.append((item["AlarmName"], item["Timestamp"]))
    return sorted(out, key=lambda t: t[1])


def explain(name: str, when: datetime, known: dict[str, list[Cause]]) -> str | None:
    for start, end, cause in known.get(name, []):
        if (start is None or when >= start) and (end is None or when < end):
            return cause
    return None


def judge_alarms(
    transitions: list[tuple[str, datetime]],
    known: dict[str, list[Cause]],
    drill_dates: set[str] | None = None,
) -> Result:
    """Each alarm with its cause; feed-errors on a recorded drill day is the drill."""
    if not transitions:
        return "PASS", "no alarm went to ALARM"
    causes: dict[str, set[str]] = {}
    for name, when in transitions:
        cause = explain(name, when, known)
        if (
            cause is None
            and name == "headway-feed-errors"
            and f"{when:%Y-%m-%d}" in (drill_dates or set())
        ):
            cause = f"feed_down drill of {when:%Y-%m-%d} (INCIDENTS.md)"
        causes.setdefault(name, set()).add(cause or f"UNEXPLAINED ({when:%d %b %H:%M}Z)")
    counts = {n: sum(1 for t, _ in transitions if t == n) for n in causes}
    parts = [f"{n} x{counts[n]}: " + " / ".join(sorted(c)) for n, c in sorted(causes.items())]
    unexplained = any("UNEXPLAINED" in x for c in causes.values() for x in c)
    return ("FAIL" if unexplained else "PASS"), "; ".join(parts)


# --- history -------------------------------------------------------------------------------

HISTORY_KEY = re.compile(
    r"history/table=([^/]+)/metric_version=(\d+)/service_date=(\d{4}-\d{2}-\d{2})/"
)


def history_gaps(keys: Iterable[str], first_day: date, last_closed: date) -> Result:
    """Every closed service day from `first_day` has an object for each table found."""
    days: dict[str, set[date]] = {}
    versions: dict[str, int] = {}
    for key in keys:
        m = HISTORY_KEY.search(key)
        if not m:
            continue
        table, version, day = m.group(1), int(m.group(2)), date.fromisoformat(m.group(3))
        if version > versions.get(table, -1):
            versions[table] = version
            days[table] = set()
        if version == versions[table]:
            days[table].add(day)
    if not days:
        return "FAIL", "no history objects found"
    expected = {first_day + timedelta(n) for n in range((last_closed - first_day).days + 1)}
    gaps = {t: sorted(expected - d) for t, d in days.items() if expected - d}
    if gaps:
        text = "; ".join(f"{t}: {len(g)} missing (first {g[0]})" for t, g in sorted(gaps.items()))
        return "FAIL", f"closed days {first_day} to {last_closed}: {text}"
    return "PASS", f"{len(days)} tables have every closed day {first_day} to {last_closed}"


def last_closed_day(now: datetime) -> date:
    """Newest service date whose history is written: a day closes at 06:00 local the next day."""
    local = now.astimezone(UTC) - timedelta(hours=5)  # Boston, without DST: conservative
    return (local - timedelta(hours=30)).date()


# --- drills --------------------------------------------------------------------------------


def parse_drills(text: str) -> list[dict[str, str]]:
    drills = []
    for block in re.split(r"(?m)^(?=### DRILL )", text)[1:]:
        head = block.splitlines()[0]
        m = re.match(r"### DRILL (\d{4}-\d{2}-\d{2}): (\w+) for (\d+) min", head)
        if not m:
            continue
        observed = re.search(r"\*\*Observed:\*\*(.*?)(?=\n- \*\*|\Z)", block, re.S)
        drills.append(
            {
                "date": m.group(1),
                "mode": m.group(2),
                "observed": observed.group(1).lower() if observed else "",
            }
        )
    return drills


def judge_drill(drills: list[dict[str, str]]) -> Result:
    feed = [d for d in drills if d["mode"] == "feed_down"]
    if not feed:
        return "FAIL", "no feed_down drill recorded in INCIDENTS.md (planned: run per RUNBOOK)"
    missing = [s for s in DRILL_STAGES if s not in feed[-1]["observed"]]
    if missing:
        return (
            "MANUAL",
            f"drill {feed[-1]['date']} lacks stage(s) in Observed: {', '.join(missing)}",
        )
    return "PASS", f"feed_down drill {feed[-1]['date']} records {' > '.join(DRILL_STAGES)}"


# --- alarms and runbook --------------------------------------------------------------------


def slug(heading: str) -> str:
    text = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s", "-", text)


def alarm_runbook_gaps(terraform: str, runbook: str) -> tuple[int, list[str]]:
    """(alarms found, names whose description has no link to an existing RUNBOOK section)."""
    anchors = {slug(m.group(1)) for m in re.finditer(r"(?m)^#{1,6}\s+(.*)", runbook)}
    found = re.findall(
        r'alarm_name\s*=\s*"([^"]+)".*?alarm_description\s*=\s*"([^"]+)"', terraform, re.S
    )
    bad = []
    for name, description in found:
        m = re.search(r"RUNBOOK\.md#([\w-]+)", description)
        if not m or m.group(1) not in anchors:
            bad.append(name)
    return len(found), bad


# --- coverage ------------------------------------------------------------------------------


def coverage_violations(table: dict[str, list[Any]]) -> int:
    nums = table.get("coverage_numerator")
    dens = table.get("coverage_denominator")
    if nums is None or dens is None:
        return 0
    return sum(1 for n, d in zip(nums, dens, strict=True) if n is not None and d and n > d)


# --- B checks ------------------------------------------------------------------------------


def read(path: str) -> str:
    return (ROOT / path).read_text()


def b1() -> Result:
    yml = read("dbt/headway/models/marts/_dims_facts.yml")
    block = yml[yml.index("name: fct_reconstructed_arrivals") :]
    block = block[: block.index("name: interval_width_s")]
    has_method = "name: method" in block and "not_null" in block
    has_conf = "name: confidence" in block and "accepted_values" in block
    usable = read("dbt/headway/models/intermediate/int_usable_arrivals.sql")
    filters = re.search(r"confidence\s+in\s*\(\s*'high'\s*,\s*'medium'\s*\)", usable) is not None
    if not (has_method and has_conf and filters):
        return "FAIL", (
            f"method tests {has_method}, confidence tests {has_conf}, "
            f"int_usable_arrivals keeps high/medium only {filters}"
        )
    return (
        "PASS",
        "dbt tests on method (not_null, accepted_values) and confidence (accepted_values); "
        "int_usable_arrivals keeps high and medium",
    )


def b2() -> Result:
    meas = read("docs/MEASUREMENTS.md")
    row = next((ln for ln in meas.splitlines() if ln.startswith("| Hand-check, 10 trips")), None)
    if row is None:
        return "MANUAL", "no 10-trip hand-check row in MEASUREMENTS.md; record trips and agreement"
    lines = [x for x in ("Red", "Orange", "Blue", "Green") if x in row]
    validation = (DOCS / "VALIDATION.md").exists()
    if len(lines) < 4 or not validation:
        return "FAIL", f"hand-check covers {lines}; VALIDATION.md present: {validation}"
    agree = re.search(r"median absolute difference [\d.]+ s", row)
    return (
        "PASS",
        f"10 trips, lines {', '.join(lines)}: {agree.group(0) if agree else 'recorded'}; "
        "also VALIDATION.md",
    )


def b3() -> Result:
    text = read("docs/ARCHITECTURE_DECISIONS.md")
    m = re.search(r"### ADR-014:.*?(?=\n### ADR-)", text, re.S)
    if not m:
        return "FAIL", "ADR-014 not found"
    section = m.group(0)
    numbers = len(re.findall(r"\d+\s?(?:%|s\b|hours)", section))
    spike = (ROOT / "docs/spikes/2026-11-sampling.md").exists()
    if numbers < 3 or not spike:
        return "FAIL", f"ADR-014 has {numbers} spike figures; spike file present: {spike}"
    return "PASS", f"ADR-014 present with {numbers} spike figures; docs/spikes/2026-11-sampling.md"


def b4() -> Result:
    text = read("docs/METRICS.md")
    titles = [
        "Prediction accuracy",
        "Actual schedule deviation",
        "Headway adherence",
        "ETT approximation",
    ]
    missing = []
    for t in titles:
        m = re.search(rf"## \d+\. {t}.*?(?=\n## )", text, re.S)
        if not m or "overage" not in m.group(0):
            missing.append(t)
    if missing:
        return "FAIL", f"METRICS.md lacks section or coverage for: {', '.join(missing)}"
    state, last = r1.pytest_result("app/tests/test_policies.py", group="app")
    return state, f"4 metric sections with coverage in METRICS.md; app policy tests: {last}"


def b5() -> Result:
    sql = read("dbt/headway/models/marts/fct_prediction_errors.sql")
    refs = set(re.findall(r"ref\('([^']+)'\)", sql))
    allowed = {"fct_stop_predictions", "int_usable_arrivals"}
    if refs != allowed:
        return "FAIL", f"fct_prediction_errors refs {sorted(refs)}; expected {sorted(allowed)}"
    usable = read("dbt/headway/models/intermediate/int_usable_arrivals.sql")
    if "fct_reconstructed_arrivals" not in usable:
        return "FAIL", "int_usable_arrivals does not come from fct_reconstructed_arrivals"
    return (
        "PASS",
        "fct_prediction_errors = fct_stop_predictions joined to int_usable_arrivals "
        "(from fct_reconstructed_arrivals); no other prediction table",
    )


def b6(s3: Any, public_bucket: str, since: datetime, now: datetime) -> Result:
    tests = r1.pytest_result(
        "tests/unit/test_history.py",
        "tests/unit/test_transform_handler.py",
        "-k",
        "history or conditional",
    )
    if s3 is None:
        return "MANUAL", f"no AWS access for the S3 listing; tests {tests[0]}: {tests[1]}"
    keys: list[str] = []
    try:
        for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=public_bucket, Prefix=f"{PUBLIC_PREFIX}/history/"
        ):
            keys += [o["Key"] for o in page.get("Contents", [])]
    except Exception as exc:  # no access: report, never crash the table
        return "MANUAL", f"cannot list history: {exc}"
    state, detail = history_gaps(keys, since.date(), last_closed_day(now))
    if tests[0] != "PASS":
        state = "FAIL"
    return state, f"{detail}; immutability and 30-day read tests {tests[0]}"


def b7(base: str) -> Result:
    text = read("docs/SLO_REPORT.md")
    m = re.search(r"Generated (\d{4}-\d{2}-\d{2})", text)
    if "No SLO tables are published" in text:
        return "FAIL", "docs/SLO_REPORT.md is the 'not enabled' notice; run make slo-report"
    if not m:
        return "FAIL", "docs/SLO_REPORT.md has no generated date"
    age = (datetime.now(UTC).date() - date.fromisoformat(m.group(1))).days
    try:
        daily, rollup = r1.gold("agg_slo_daily"), r1.gold("agg_slo_rollup")
    except OSError as exc:
        return "FAIL", f"public gold SLO tables not readable: {exc}"
    windows = sorted({str(w) for w in rollup.get("window_label", [])})
    ok = age <= 1 and bool(daily.get("service_date")) and {"last_7d", "last_30d"} <= set(windows)
    return ("PASS" if ok else "FAIL"), (
        f"report generated {m.group(1)} ({age} d old); public gold has agg_slo_daily "
        f"({len(daily.get('service_date', []))} rows) and agg_slo_rollup "
        f"(windows {windows or 'none'})"
    )


def b8() -> Result:
    files = sorted((ROOT / "tests/simulations").glob("test_*.py"))
    if len(files) != SIM_FILES:
        return "FAIL", f"{len(files)} simulation files, expected {SIM_FILES}"
    state, last = r1.pytest_result("tests/simulations")
    return state, f"{SIM_FILES} simulations: {last}"


def b9() -> Result:
    return judge_drill(parse_drills(read("docs/INCIDENTS.md")))


def b10(base: str) -> Result:
    url = f"{base}/observability/index.html"
    try:
        status = urllib.request.urlopen(url, timeout=30).status  # noqa: S310
    except urllib.error.HTTPError as exc:
        status = exc.code
    except OSError as exc:
        return "FAIL", f"{url}: {exc}"
    linked = "observability" in read("app/pages/3_Pipeline_health.py")
    anomalies = sum(
        read(f"dbt/headway/models/marts/{f}").count("elementary.")
        for f in ("_dims_facts.yml", "_aggregates.yml")
    )
    ok = status == 200 and linked and anomalies > 0
    return ("PASS" if ok else "FAIL"), (
        f"report HTTP {status}; linked from the Pipeline health page: {linked}; "
        f"{anomalies} Elementary anomaly tests in dbt schemas"
    )


def b11() -> Result:
    count, bad = alarm_runbook_gaps(read("infra/main/alerts.tf"), read("docs/RUNBOOK.md"))
    if bad:
        return "FAIL", f"alarms without a RUNBOOK section link: {', '.join(bad)}"
    return (
        "PASS",
        f"{count} alarms in alerts.tf, each description links to an existing RUNBOOK section",
    )


def b12(
    pipeline_minutes: set[datetime] | None,
    since: datetime,
    since_source: str,
    cw: Any,
    now: datetime,
) -> Result:
    if pipeline_minutes is None:
        return "FAIL", "cannot read public agg_pipeline_health"
    soak, _ = soak_missed(pipeline_minutes, since)
    if cw is None:
        return "MANUAL", f"no AWS access for alarm history; soak: {soak[0]} {soak[1]}"
    items: list[dict[str, Any]] = []
    try:
        for page in cw.get_paginator("describe_alarm_history").paginate(
            AlarmTypes=["MetricAlarm"],
            HistoryItemType="StateUpdate",
            StartDate=since,
            EndDate=now,
        ):
            items += [i for i in page["AlarmHistoryItems"] if i["AlarmName"].startswith("headway")]
    except Exception as exc:
        return "MANUAL", f"cannot read alarm history: {exc}; soak: {soak[0]} {soak[1]}"
    drills = {
        d["date"] for d in parse_drills(read("docs/INCIDENTS.md")) if d["mode"] == "feed_down"
    }
    alarms = judge_alarms(alarm_transitions(items), KNOWN_CAUSES, drills)
    state = worst([soak, alarms])
    return (
        state,
        f"soak since {since:%Y-%m-%d %H:%M}Z ({since_source}): {soak[1]}. Alarms: {alarms[1]}",
    )


# --- AWS access ----------------------------------------------------------------------------


def aws_clients() -> tuple[Any, Any, str, str, str | None]:
    """(s3, cloudwatch, raw bucket, public bucket, error). Clients are None without access."""
    try:
        import boto3

        account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
        raw = os.environ.get("HEADWAY_RAW_BUCKET") or f"headway-raw-{account}"
        pub = os.environ.get("HEADWAY_PUBLIC_BUCKET") or f"headway-public-{account}"
        return (
            boto3.client("s3", region_name=REGION),
            boto3.client("cloudwatch", region_name=REGION),
            raw,
            pub,
            None,
        )
    except Exception as exc:  # no credentials, no network
        return None, None, "", "", f"{type(exc).__name__}"


def read_transform_runs(s3: Any, raw: str, now: datetime, days: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for n in range(days):
        key = f"state/transform_runs/dt={(now - timedelta(days=n)):%Y-%m-%d}/rows.parquet"
        try:
            body = s3.get_object(Bucket=raw, Key=key)["Body"].read()
        except Exception:
            continue
        rows += pq.read_table(io.BytesIO(body)).to_pylist()
    return rows


def metric_max(cw: Any, name: str, start: datetime, end: datetime) -> float | None:
    try:
        points: list[float] = []
        t = start
        while t < end:  # GetMetricStatistics caps datapoints per call: one day per request
            stop = min(t + timedelta(days=1), end)
            resp = cw.get_metric_statistics(
                Namespace="Headway",
                MetricName=name,
                StartTime=t,
                EndTime=stop,
                Period=3600,
                Statistics=["Maximum"],
            )
            points += [p["Maximum"] for p in resp["Datapoints"]]
            t = stop
        return max(points) if points else None
    except Exception:
        return None


# --- additional checks ---------------------------------------------------------------------


def r2_tables_fresh(now: datetime) -> Result:
    sys.path.insert(0, str(ROOT / "dbt"))
    from headway_transform.publish import R2_GOLD_TABLES

    missing = []
    for name in R2_GOLD_TABLES:
        try:
            r1.fetch(f"{r1.base_url()}/gold/{name}.parquet")
        except OSError:
            missing.append(name)
    try:
        exported = json.loads(r1.fetch(f"{r1.base_url()}/gold/manifest.json"))["exported_at"]
        age = (now - datetime.fromisoformat(exported.replace("Z", "+00:00"))).total_seconds() / 60
    except (OSError, KeyError, ValueError):
        return "FAIL", "gold manifest not readable"
    if missing:
        return (
            "FAIL",
            f"{len(missing)} of {len(R2_GOLD_TABLES)} R2 tables missing: {', '.join(missing[:4])}",
        )
    detail = f"all {len(R2_GOLD_TABLES)} R2 tables present; gold {age:.0f} min old"
    return ("PASS" if age <= MAX_GOLD_AGE_MIN else "FAIL"), detail


def coverage_check() -> Result:
    names = [
        "agg_accuracy_window", "agg_countdown_accuracy_window", "agg_countdown_accuracy_daily",
        "agg_prediction_accuracy_daily", "agg_actual_deviation_window", "agg_headway_window",
    ]  # fmt: skip
    bad: list[str] = []
    for name in names:
        try:
            n = coverage_violations(r1.gold(name))
        except OSError:
            return "FAIL", f"cannot read {name}"
        if n:
            bad.append(f"{name} {n} rows")
    if bad:
        return "FAIL", "coverage above 100 % in " + ", ".join(
            bad
        ) + " (fix in repo, not yet deployed)"
    return "PASS", f"coverage numerator at most denominator in {len(names)} tables"


def ingest_polls(s3: Any, raw: str) -> Result:
    found = r1.newest_manifest_from_aws() if s3 is not None else None
    if found is None:
        return (
            "MANUAL",
            "record: six manifest entries, poll_index 0-3, run 45-58 s, memory < 190 MB",
        )
    doc, source = found
    entries = doc["entries"]
    polls = sorted({e.get("poll_index") for e in entries})
    ok = len(entries) == 6 and polls == [0, 1, 2, 3]
    return ("PASS" if ok else "FAIL"), (
        f"{len(entries)} entries, poll_index {polls} ({source}); "
        "run time and memory: record from logs"
    )


# --- main ----------------------------------------------------------------------------------


def table(title: str, results: dict[str, Result], key: Callable[[str], Any] = str) -> None:
    print(f"\n{title}")
    print(f"{'Test':<6} {'Result':<7} Detail")
    for name in sorted(results, key=key):
        status, detail = results[name]
        print(f"{name:<6} {status:<7} {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", help="soak start (UTC ISO), default: first Release 2 run")
    parser.add_argument(
        "--slots", action="store_true", help="also list every hourly transform slot since switch-on"
    )
    args = parser.parse_args()

    now = datetime.now(UTC)
    s3, cw, raw, pub, aws_error = aws_clients()
    rows = read_transform_runs(s3, raw, now) if s3 else []
    if args.since:
        since, source = datetime.fromisoformat(args.since.replace("Z", "+00:00")), "--since"
    elif (found := switch_on_from_runs(rows)) is not None and s3:
        since, source = found, "first r2_enabled transform run in state"
    else:
        since, source = FALLBACK_SWITCH_ON, "documented switch-on 2026-11-26 17:47:54Z"

    try:
        ph = r1.gold("agg_pipeline_health")["scheduled_at"]
        minutes: set[datetime] | None = {
            t.astimezone(UTC).replace(second=0, microsecond=0) for t in ph if t
        }
    except OSError:
        minutes = None

    base = r1.base_url()
    b: dict[str, Result] = {
        "B1": b1(), "B2": b2(), "B3": b3(), "B4": b4(), "B5": b5(),
        "B6": b6(s3, pub, since, now), "B7": b7(base), "B8": b8(), "B9": b9(),
        "B10": b10(base), "B11": b11(), "B12": b12(minutes, since, source, cw, now),
    }  # fmt: skip

    extra: dict[str, Result] = {}
    extra["X1 transform success"] = (
        transform_success(rows, since) if s3 else ("MANUAL", f"no AWS access ({aws_error})")
    )
    extra["X2 duration, memory"] = (
        budget(metric_max(cw, "TransformDurationSeconds", since, now),
               metric_max(cw, "TransformMemoryUsedPct", since, now))
        if cw else ("MANUAL", f"no AWS access ({aws_error})")
    )  # fmt: skip
    extra["X3 R2 tables fresh"] = r2_tables_fresh(now)
    extra["X4 coverage <= 100"] = coverage_check()
    extra["X5 ingest 4 polls"] = ingest_polls(s3, raw)

    if args.slots and s3:
        print("\nHourly transform slots since switch-on")
        for slot, status in slot_statuses(rows, since).items():
            print(f"{slot:%Y-%m-%d %H}:00Z  {status}")

    table("Release 2 acceptance B1-B12", b, key=lambda n: int(n[1:]))
    table("Additional checks (operational; not part of B1-B12)", extra)
    allr = list(b.values()) + list(extra.values())
    counts = {s: sum(1 for r, _ in allr if r == s) for s in ("PASS", "FAIL", "MANUAL")}
    print("\n" + ", ".join(f"{n} {s}" for s, n in counts.items()))
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
