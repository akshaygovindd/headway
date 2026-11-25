"""Incident summaries in the transform (Release 3, ADR-008); only with HEADWAY_ENABLE_R3=true.

After the dbt build, ``summarize`` writes the text of ``agg_incidents.summary_text``:

* An incident needs a summary when its version (``evidence_hash``) has none yet and either it is
  closed or it is open and its last summary is at least three hours old (or it has none).
* The text comes from an LLM behind the ``LlmClient`` interface (OpenAI by default, model name in
  ``HEADWAY_INCIDENT_LLM_MODEL``, key from SSM ``/headway/openai_api_key``) that receives only the
  evidence JSON and a fixed instruction, or from the deterministic template when there is no key,
  the daily call cap is spent or the call fails.
* Every LLM text goes through the blocking number and causal check (``incident_numcheck``); a text
  that fails is thrown away, the template is used and the rejection is counted.
* Summaries persist in the raw bucket (``state/incident_summaries``), so a version is summarised
  once, and the daily cap counts calls across runs. The step never fails the run.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from headway_transform import duckdb_limits, incident_numcheck, state

logger = logging.getLogger()

SSM_API_KEY = "/headway/openai_api_key"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_DAILY_CAP = 50
OPEN_REFRESH = timedelta(hours=3)
LOW_COVERAGE_PCT = 70.0
MAX_OUTPUT_TOKENS = 300

INSTRUCTION = (
    "You summarise one public-transit service incident for riders. The user message is the "
    "incident's evidence as JSON. Describe what changed, where and when, in two to four "
    "sentences, using only numbers that appear in the evidence. Do not state or guess causes: you "
    "may quote an alert header from the evidence, in double quotes, exactly as written. If the "
    "evidence shows "
    "low arrival coverage, say that the values are less certain. Do not add anything that is not "
    "in the evidence."
)

INCIDENT_SUMMARIES = state.StateTable(
    name="incident_summaries",
    folder="incident_summaries",
    timestamp="generated_at",
    columns={
        "incident_id": "varchar",
        "evidence_hash": "varchar",
        "generated_at": "timestamptz",
        "summary_text": "varchar",
        "template_used": "boolean",
        "number_check": "varchar",  # passed | rejected | not_run
        "rejection_reason": "varchar",
        "llm_model": "varchar",
        "llm_called": "boolean",
    },
    retention_days=35,
)


class LlmClient(Protocol):
    """The provider interface: one completion from a fixed instruction and the evidence JSON."""

    model: str

    def complete(self, instruction: str, evidence_json: str) -> str: ...


class LlmError(RuntimeError):
    """The provider call failed (network, status, malformed answer)."""


class OpenAIClient:
    """Chat completions over HTTPS with the standard library (no SDK in the image)."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 20.0,
    ) -> None:
        self._key = api_key
        self.model = model
        self._url = f"{base_url}/chat/completions"
        self._timeout = timeout

    def complete(self, instruction: str, evidence_json: str) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "max_completion_tokens": MAX_OUTPUT_TOKENS,
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": evidence_json},
                ],
            }
        ).encode()
        req = urllib.request.Request(  # noqa: S310 - fixed https endpoint
            self._url,
            data=body,
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                doc = json.loads(resp.read())
            return str(doc["choices"][0]["message"]["content"]).strip()
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
            raise LlmError(type(exc).__name__) from exc


def load_api_key(ssm: Any) -> str | None:
    """The key from SSM, or None when it is absent or unreadable (the template is used)."""
    try:
        value = ssm.get_parameter(Name=SSM_API_KEY, WithDecryption=True)["Parameter"]["Value"]
    except Exception:  # not created yet, no permission, no network: all end in the template
        logger.info("no OpenAI key available; incident summaries use the template")
        return None
    return str(value).strip() or None


def build_client(ssm: Any) -> LlmClient | None:
    key = load_api_key(ssm)
    if key is None:
        return None
    return OpenAIClient(key, os.environ.get("HEADWAY_INCIDENT_LLM_MODEL", DEFAULT_MODEL))


def daily_cap() -> int:
    return int(os.environ.get("HEADWAY_INCIDENT_LLM_DAILY_CAP", DEFAULT_DAILY_CAP))


# --- the deterministic template -------------------------------------------------------------


def _hours_word(n: int) -> str:
    return f"{n} hour" if n == 1 else f"{n} hours"


def template_summary(evidence: dict[str, Any]) -> str:
    """The summary written from the evidence alone: every number is an evidence value."""
    hours = evidence.get("hours", [])
    direction = evidence.get("direction_id")
    where = "all directions" if direction is None else f"direction {direction}"
    start = hours[0]["hour"] if hours else evidence["start"]
    state_text = ", still open" if evidence.get("is_open") else ""
    parts = [
        f"{evidence['line']} ({where}): incident from {start} to {evidence['end']} "
        f"({_hours_word(int(evidence['n_hours']))}{state_text})."
    ]

    def crossed(rule: str) -> list[dict[str, Any]]:
        return [h for h in hours if rule in h.get("crossed", [])]

    if crossed("deviation"):
        worst = max(crossed("deviation"), key=lambda h: h["deviation_over_baseline_s"])
        parts.append(
            f"The median deviation from the timetable reached {worst['median_deviation_s']:.0f} s "
            f"at {worst['hour']}, {worst['deviation_over_baseline_s']:.0f} s above the usual "
            f"{worst['baseline_median_s']:.0f} s for this day type and hour."
        )
    if crossed("gap"):
        worst = max(crossed("gap"), key=lambda h: h["gap_share_pct"])
        parts.append(
            f"The share of headway gaps reached {worst['gap_share_pct']:.0f}% at {worst['hour']}."
        )
    if crossed("skipped"):
        worst = max(crossed("skipped"), key=lambda h: h["skipped_share_pct"])
        parts.append(
            f"The share of skipped stops reached {worst['skipped_share_pct']:.0f}% "
            f"at {worst['hour']}."
        )
    alerts = [a for a in evidence.get("alerts", []) if a.get("header")]
    if crossed("alert"):
        if alerts:
            for alert in alerts[:2]:
                parts.append(f'A service alert was active: "{alert["header"]}".')
        else:
            parts.append("A line-wide service-disruption alert was active.")
    if evidence.get("data_outage_hours_inside"):
        parts.append(
            f"{_hours_word(int(evidence['data_outage_hours_inside']))} inside the window had a "
            "data outage and were not judged."
        )
    if str(evidence.get("baseline_rule", "")).startswith("disabled"):
        parts.append(
            "The comparison with the usual deviation was off for some hours: too few earlier "
            "days of this day type."
        )
    cov = evidence.get("min_coverage_pct")
    if cov is not None:
        low = ", so these values are less certain" if cov < LOW_COVERAGE_PCT else ""
        parts.append(f"Arrival coverage was {cov:.0f}% at its lowest{low}.")
    return " ".join(parts)


# --- the step -------------------------------------------------------------------------------


@dataclass
class Outcome:
    text: str
    template_used: bool
    number_check: str  # passed | rejected | not_run
    reason: str | None
    model: str | None
    llm_called: bool


def make_summary(
    evidence_json: str, client: LlmClient | None, *, may_call: bool, why_not: str
) -> Outcome:
    """One incident version's summary: the LLM's when it passes the check, else the template."""
    evidence = json.loads(evidence_json)
    template = template_summary(evidence)
    if client is None or not may_call:
        return Outcome(template, True, "not_run", why_not, None, False)
    try:
        text = client.complete(INSTRUCTION, evidence_json)
    except Exception as exc:
        logger.warning("incident summary call failed: %s", type(exc).__name__)
        return Outcome(template, True, "not_run", f"llm_error: {type(exc).__name__}", client.model,
                       True)  # fmt: skip
    result = incident_numcheck.check(text, evidence)
    if not result.ok:
        return Outcome(template, True, "rejected", result.reason[:500], client.model, True)
    return Outcome(text, False, "passed", None, client.model, True)


def summarize(
    s3: Any,
    bucket: str,
    db_path: str,
    now: datetime,
    *,
    client: LlmClient | None,
    cap: int | None = None,
    no_client_reason: str = "no_api_key",
) -> dict[str, Any]:
    """Fill the summary columns of agg_incidents; returns the step's summary."""
    cap = daily_cap() if cap is None else cap
    state.load(s3, bucket, db_path, INCIDENT_SUMMARIES, now)
    con = duckdb_limits.connect(db_path)
    new_rows: list[tuple[Any, ...]] = []
    summary: dict[str, Any] = {"incident_summary_status": "ok"}
    try:
        con.execute("set TimeZone = 'UTC'")
        tables = {
            r[0]
            for r in con.execute(
                "select table_name from information_schema.tables where table_schema = 'main'"
            ).fetchall()
        }
        if "agg_incidents" not in tables:
            return {"incident_summary_status": "not_built"}
        incidents = con.execute(
            "select incident_id, evidence_hash, is_open, evidence_json from agg_incidents "
            "order by incident_start_ts, incident_id"
        ).fetchall()
        stored = {
            (r[0], r[1])
            for r in con.execute(
                "select incident_id, evidence_hash from main.incident_summaries"
            ).fetchall()
        }
        # epoch seconds: fetching a timestamptz would need pytz in the Python client
        latest: dict[str, datetime] = {
            r[0]: datetime.fromtimestamp(r[1], UTC)
            for r in con.execute(
                "select incident_id, epoch(max(generated_at)) from main.incident_summaries "
                "group by 1"
            ).fetchall()
        }
        today = now.astimezone(UTC).date()
        row = con.execute(
            "select count(*) from main.incident_summaries "
            "where llm_called and cast(generated_at at time zone 'UTC' as date) = ?",
            [today],
        ).fetchone()
        calls_today = int(row[0]) if row else 0
        counts = {"generated": 0, "llm": 0, "template": 0, "rejected": 0, "errors": 0, "capped": 0}
        for incident_id, version, is_open, evidence_json in incidents:
            if (incident_id, version) in stored:
                continue
            last = latest.get(incident_id)
            if is_open and last is not None and now - last < OPEN_REFRESH:
                continue  # an open incident is re-summarised every three hours
            may_call = calls_today < cap
            outcome = make_summary(
                evidence_json, client, may_call=may_call,
                why_not=no_client_reason if client is None else "daily_cap_reached",
            )  # fmt: skip
            if client is not None and not may_call:
                counts["capped"] += 1
            if outcome.llm_called:
                calls_today += 1
            counts["generated"] += 1
            counts["template" if outcome.template_used else "llm"] += 1
            counts["rejected"] += outcome.number_check == "rejected"
            counts["errors"] += (outcome.reason or "").startswith("llm_error")
            new_rows.append(
                (incident_id, version, now, outcome.text, outcome.template_used,
                 outcome.number_check, outcome.reason, outcome.model, outcome.llm_called)
            )  # fmt: skip
        if new_rows:
            con.executemany(
                "insert into main.incident_summaries values (?, ?, ?, ?, ?, ?, ?, ?, ?)", new_rows
            )
        con.execute(
            """
            update agg_incidents a
            set summary_text = s.summary_text, template_used = s.template_used,
                number_check = s.number_check, rejection_reason = s.rejection_reason,
                llm_model = s.llm_model, summary_generated_at = s.generated_at,
                summary_evidence_hash = s.evidence_hash,
                summary_stale = s.evidence_hash <> a.evidence_hash
            from (
                select * from main.incident_summaries
                qualify row_number() over (
                    partition by incident_id order by generated_at desc
                ) = 1
            ) s
            where a.incident_id = s.incident_id
            """
        )
        summary.update(
            {
                "incident_count": len(incidents),
                "incident_summaries_generated": counts["generated"],
                "incident_summaries_llm": counts["llm"],
                "incident_summaries_template": counts["template"],
                "incident_llm_calls": sum(1 for r in new_rows if r[8]),
                "incident_llm_rejections": counts["rejected"],
                "incident_llm_errors": counts["errors"],
                "incident_llm_capped": counts["capped"],
            }
        )
    finally:
        con.close()
    if new_rows:
        state.append(s3, bucket, INCIDENT_SUMMARIES, new_rows, now)
    return summary


# --- smoke support (image smoke test only; reached through HEADWAY_SMOKE_* variables) ---------


def _smoke_evidence(line: str, start: str, end: str) -> dict[str, Any]:
    hours = [
        {"hour": start, "date": "2026-11-22", "median_deviation_s": 420.0,
         "baseline_median_s": 40.0, "deviation_over_baseline_s": 380.0, "gap_share_pct": 35.0,
         "skipped_share_pct": 2.0, "coverage_pct": 88.0, "alert_active": False,
         "crossed": ["deviation", "gap"]},
        {"hour": end, "date": "2026-11-22", "median_deviation_s": 300.0,
         "baseline_median_s": 40.0, "deviation_over_baseline_s": 260.0, "gap_share_pct": 12.0,
         "skipped_share_pct": 1.0, "coverage_pct": 90.0, "alert_active": False,
         "crossed": ["deviation"]},
    ]  # fmt: skip
    return {
        "line": line, "direction_id": 0, "date": "2026-11-22", "start": start, "end": "17:00",
        "is_open": False, "n_hours": 2, "hours": hours,
        "thresholds": {"deviation_over_baseline_s": 180, "gap_share_pct": 30,
                       "skipped_share_pct": 20, "min_hours": 2},
        "baseline_rule": "enabled", "baseline_days": 20, "min_coverage_pct": 88.0,
        "mean_coverage_pct": 89.0, "data_outage_hours_inside": 0, "alerts": [],
    }  # fmt: skip


def seed_smoke_incidents(db_path: str) -> int:
    """Two synthetic incidents in agg_incidents (the smoke sample is too short to make any)."""
    con = duckdb_limits.connect(db_path)
    try:
        con.execute("delete from agg_incidents")
        for i, line in enumerate(("Red", "Blue")):
            evidence = json.dumps(_smoke_evidence(line, "15:00", "16:00"))
            con.execute(
                "insert into agg_incidents (incident_id, evidence_hash, line, direction_id, "
                "incident_start_ts, incident_end_ts, n_hours, is_open, thresholds_crossed, "
                "min_coverage_pct, mean_coverage_pct, n_outage_hours_inside, evidence_json, "
                "match_status) values (?, ?, ?, 0, '2026-11-22 20:00:00+00', "
                "'2026-11-22 22:00:00+00', 2, false, 'deviation,gap', 88.0, 89.0, 0, ?, "
                "'unmatched_incident')",
                [f"smoke-{i}", f"smoke-hash-{i}", line, evidence],
            )
    finally:
        con.close()
    return 2


class SmokeLlm:
    """Stands in for the provider in the image smoke test: the first answer states a number that
    is not in the evidence (the check must reject it), the others are valid."""

    model = "smoke-fake"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, instruction: str, evidence_json: str) -> str:
        self.calls += 1
        if self.calls == 1:
            return "Delays reached 99999 s on the line."
        return template_summary(json.loads(evidence_json))
