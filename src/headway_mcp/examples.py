"""Curated example questions with answers precomputed from the tools, no LLM (ADR-021).

``precompute`` runs the tools over an export and writes the answers as ``analyst_examples.json``
(the transform does so into gold when Release 3 is on). A template phrases each answer from the
tool rows, so every number in it is a result value, and the result's provenance travels with it.
The page shows these when there is no key, the budget is spent or a session is over its limit.
"""

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from headway_mcp.catalog import METRICS
from headway_mcp.tools import Tools

FILE = "gold/analyst_examples.json"
SCHEMA = 1


def _num(v: Any, unit: str) -> str:
    return f"{v:g} {unit}" if unit != "%" else f"{v:g}%"


def _unit(metric: str) -> str:
    return "%" if metric.endswith("_pct") else "s"


def _say_one(res: dict[str, Any], line: str, metric: str, window: str) -> str:
    row = res["rows"][0] if res["rows"] else None
    label = METRICS[metric].label
    if row is None or row[metric] is None:
        return (
            f"There is not enough data: arrival coverage for {line} was under 50%, so Headway "
            "withholds this value."
        )
    value = _num(row[metric], _unit(metric))
    text = f"{label} for {line} over {window.replace('_', ' ')}: {value}"
    if metric == "coverage_pct":
        return text + "."
    return text + f", at {row['coverage_pct']:g}% arrival coverage."


def _say_compare(res: dict[str, Any], metric: str, window: str) -> str:
    parts = [
        f"{r['line']} {_num(r[metric], _unit(metric))}"
        if r[metric] is not None
        else f"{r['line']} withheld (coverage under 50%)"
        for r in res["rows"]
    ]
    return f"{METRICS[metric].label}, {window.replace('_', ' ')}: " + "; ".join(parts) + "."


def _say_alerts(res: dict[str, Any], line: str) -> str:
    if not res["rows"]:
        return f"No service alert was active on {line} in the period."
    parts = [f"{r['date']}: {r['effect'].replace('_', ' ').lower()} ({r['scope']} scope)"
             for r in res["rows"][:5]]  # fmt: skip
    return f"Service alerts active on {line}: " + "; ".join(parts) + "."


def _say_health(res: dict[str, Any]) -> str:
    parts = [
        f"{r['date']}: {r['runs']} runs, {r['vehicle_polls_error']} failed vehicle polls, "
        f"{r['quarantined_records']} quarantined records"
        for r in res["rows"]
    ]
    return "Pipeline health: " + "; ".join(parts) + "." if parts else "No pipeline runs."


def precompute(tools: Tools) -> dict[str, Any]:
    """Run each example question through the tools. Examples whose tool is not available are
    left out."""
    end = tools.as_of_date()
    start7 = (end - timedelta(days=6)).isoformat()
    rng = {"start": start7, "end": end.isoformat()}
    spec: list[tuple[str, str, Callable[[], dict[str, Any]], Callable[[dict[str, Any]], str]]] = []

    def one(q: str, line: str, metric: str, window: str) -> None:
        spec.append((q, "get_line_reliability",
                     lambda: tools.get_line_reliability(line, metric, window),
                     lambda r: _say_one(r, line, metric, window)))  # fmt: skip

    def cmp(q: str, lines: list[str], metric: str, window: str) -> None:
        spec.append((q, "compare_lines", lambda: tools.compare_lines(lines, metric, window),
                     lambda r: _say_compare(r, metric, window)))  # fmt: skip

    one("How reliable was the Red Line in the last 24 hours?", "Red",
        "predicted_deviation_median_s", "last_24h")  # fmt: skip
    one("What share of Orange Line arrivals were on time this week?", "Orange", "on_time_pct",
        "last_7d")  # fmt: skip
    one("How much of the Blue Line's arrivals could Headway see in the last 24 hours?", "Blue",
        "coverage_pct", "last_24h")  # fmt: skip
    cmp("Compare the Red, Orange and Blue lines on median predicted deviation.",
        ["Red", "Orange", "Blue"], "predicted_deviation_median_s", "last_24h")  # fmt: skip
    cmp("Which subway line had the most bunched headways this week?",
        ["Red", "Orange", "Blue", "Mattapan"], "headway_bunched_pct", "last_7d")  # fmt: skip
    cmp("How do the Green Line branches compare on skipped stops?",
        ["Green-B", "Green-C", "Green-D", "Green-E"], "skipped_share_pct", "last_24h")  # fmt: skip
    spec.append(("Which service alerts were active on the Red Line this week?",
                 "get_service_alerts", lambda: tools.get_service_alerts("Red", rng),
                 lambda r: _say_alerts(r, "Red")))  # fmt: skip
    spec.append(("Were there service alerts on the Orange Line this week?",
                 "get_service_alerts", lambda: tools.get_service_alerts("Orange", rng),
                 _say_orange))  # fmt: skip
    spec.append(("Is the Headway pipeline healthy this week?", "get_pipeline_health",
                 lambda: tools.get_pipeline_health(rng), _say_health))  # fmt: skip

    items = []
    for question, tool, run, say in spec:
        res = run()
        if res.get("status") != "ok":
            continue
        items.append({"question": question, "tool": tool, "answer": say(res), "results": [res]})
    return {"schema": SCHEMA, "built_for": end.isoformat(), "examples": items}


def _say_orange(res: dict[str, Any]) -> str:
    return _say_alerts(res, "Orange")


def write(tools: Tools, path: str) -> int:
    doc = precompute(tools)
    with open(path, "w") as fh:
        json.dump(doc, fh, default=str)
    return len(doc["examples"])


def load(doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The examples of a loaded analyst_examples.json (empty when absent or another schema)."""
    if not doc or doc.get("schema") != SCHEMA:
        return []
    return list(doc.get("examples", []))
