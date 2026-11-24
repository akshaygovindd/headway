"""A scripted fake LLM for `--fake` runs: no network, deterministic.

It plays the `calls` of a question, then writes an honest summary of the tool results. With
`--inject-faults` three questions get a deliberately bad draft, which the scorer must catch.
"""

import json
from typing import Any

MODEL = "fake-scripted"
# question id -> what its draft gets wrong
FAULTS = {
    "ml01": "wrong_value",  # in the results but not the answer (passes the gate)
    "lc01": "invented_number",  # a number that is in no result (the gate blocks it)
    "lc06": "wrong_direction",  # "higher" where the delta is negative (the gate blocks it)
}
UNIT = {"s": " s", "pct": "%"}


def _unit(metric: str) -> str:
    return UNIT.get(metric.rsplit("_", 1)[-1], "")


def _num(v: float) -> str:
    return f"{v:g}"


def _metric_text(result: dict[str, Any]) -> str:
    metric = result["provenance"]["metric"]
    name = metric.rsplit("_", 1)[0].replace("_", " ")
    unit = _unit(metric)
    rows = result["rows"]
    parts: list[str] = []
    for row in rows:
        who = row.get("line", "")
        if "date" in row:
            who = f"{row['line']} on {row['date']} direction {row['direction_id']}"
        if row[metric] is None:
            parts.append(f"{who} withheld")
            continue
        parts.append(f"{who} {_num(row[metric])}{unit}")
    text = f"The {name}: " + "; ".join(parts) + "."
    delta = f"delta_{metric}"
    if len(rows) > 1 and delta in rows[0]:
        for row in rows[1:]:
            d = row.get(delta)
            if d:
                word = "higher" if d > 0 else "lower"
                text += f" {row['line']} was {word} than {rows[0]['line']}."
    return text


def _alerts_text(result: dict[str, Any]) -> str:
    effects = sorted({r["effect"].replace("_", " ").lower() for r in result["rows"]})
    if not effects:
        return "No service alert was active."
    return "Service alerts active: " + ", ".join(effects) + "."


def _health_text(result: dict[str, Any]) -> str:
    return " ".join(
        f"On {r['date']} there were {r['runs']} pipeline runs and {r['trip_update_failures']} "
        "trip update failures."
        for r in result["rows"]
    )


def honest_text(results: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for r in results:
        if r.get("status") != "ok":
            continue
        if r["provenance"].get("metric"):
            parts.append(_metric_text(r))
        elif "trip_update_failures" in (r["rows"][0] if r["rows"] else {}):
            parts.append(_health_text(r))
        else:
            parts.append(_alerts_text(r))
    return " ".join(parts) or "That is not available from my tools, so I cannot answer it."


def faulty_text(fault: str, results: list[dict[str, Any]]) -> str:
    first = results[0]["rows"][0]
    metric = results[0]["provenance"]["metric"]
    name = metric.rsplit("_", 1)[0].replace("_", " ")
    if fault == "wrong_value":
        return f"The {name}: {first['line']} {first['n_observations']}."
    if fault == "invented_number":
        return f"The {name}: {first['line']} {_num(first[metric])}{_unit(metric)}; the other 75."
    rows = results[0]["rows"]
    return (
        f"The {name}: {rows[0]['line']} {_num(rows[0][metric])}{_unit(metric)}; "
        f"{rows[1]['line']} {_num(rows[1][metric])}{_unit(metric)}, higher than {rows[0]['line']}."
    )


class ScriptedLlm:
    model = MODEL

    def __init__(self, calls: list[list[Any]], fault: str | None = None) -> None:
        self._calls = [(c[0], c[1]) for c in calls]
        self._fault = fault
        self._turns = 0

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self._turns += 1
        usage = {"prompt_tokens": 400, "completion_tokens": 60}
        if self._calls:
            name, args = self._calls.pop(0)
            call = {"id": f"c{self._turns}", "name": name, "arguments": json.dumps(args)}
            return {"content": None, "tool_calls": [call], "usage": usage}
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if self._fault and results:
            text = faulty_text(self._fault, results)
        else:
            text = honest_text(results)
        return {"content": text, "tool_calls": [], "usage": usage}
