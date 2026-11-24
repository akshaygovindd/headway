"""The report of an eval run (docs/EVALS.md) and its console summary. Plain dicts in, text out."""

import json
from typing import Any

CATEGORIES = (
    "metric_lookup",
    "line_comparison",
    "specific_date",
    "insufficient_data",
    "causal_trap",
    "service_vs_data_outage",
    "metric_not_computed",
    "out_of_scope_injection",
)


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1f}%"


def _usd(v: float | None) -> str:
    return "unknown" if v is None else f"${v:.4f}"


def console(s: dict[str, Any]) -> str:
    lines = [
        f"{s['questions']} questions, {s['passed']} passed, model {s['model']}",
        f"numeric {_pct(s['numeric_pct'])}  unsupported {_pct(s['unsupported_pct'])} "
        f"({s['unsupported']}/{s['claims']} claims)  "
        f"refusal (strict) {_pct(s['strict_refusal_pct'])}  "
        f"p90 {s['p90_s']:.2f} s  cost {_usd(s['cost_total'])}",
    ]
    lines += [f"FAIL: {f}" for f in s["failed_thresholds"]] or ["all thresholds met"]
    return "\n".join(lines)


def render(s: dict[str, Any], results: list[dict[str, Any]], thresholds: dict[str, float]) -> str:
    out: list[str] = ["# Analyst evals", ""]
    if s["fake"]:
        out += [
            "> **FAKE RUN**: a scripted model tests the scorer. These numbers say nothing "
            "about a real model. Do not commit this file.",
            "",
        ]
    if s["partial"] or s["incomplete"]:
        out += [f"> **PARTIAL RUN**: {s['questions']} of {s['total_questions']} questions.", ""]
    verdict = "PASS" if s["ok"] else "FAIL"
    out += [
        f"- **Result:** {verdict}",
        f"- **Date:** {s['date']}",
        f"- **Model:** `{s['model']}`",
        f"- **Snapshot:** `evals/snapshot/`, metrics as of {s['metrics_as_of']}",
        f"- **Questions:** {s['questions']} run, {s['passed']} passed",
        f"- **Cost:** {_usd(s['cost_total'])} in total, "
        f"{_usd(s['cost_per_question'])} per question (cap ${s['max_cost']:.2f} per run)",
        "",
        "## Thresholds",
        "",
        "| Measure | Result | Threshold | |",
        "| --- | --- | --- | --- |",
    ]

    def row(name: str, value: str, limit: str, ok: bool | None) -> str:
        mark = "n/a" if ok is None else ("pass" if ok else "**FAIL**")
        return f"| {name} | {value} | {limit} | {mark} |"

    n, u, r, p = s["numeric_pct"], s["unsupported_pct"], s["strict_refusal_pct"], s["p90_s"]
    out += [
        row("Numeric correctness", _pct(n), f">= {thresholds['numeric_pct']:g}%",
            None if n is None else n >= thresholds["numeric_pct"]),
        row("Unsupported claims", f"{_pct(u)} ({s['unsupported']} of {s['claims']})",
            f"<= {thresholds['unsupported_pct']:g}%", u <= thresholds["unsupported_pct"]),
        row("Refusal correctness (insufficient data, injection)", _pct(r), "100%",
            None if r is None else r >= thresholds["refusal_pct"]),
        row("p90 latency", f"{p:.2f} s", f"<= {thresholds['p90_s']:g} s", p <= thresholds["p90_s"]),
        "",
        "Also measured: "
        f"tool success {_pct(s['tool_success_pct'])} of calls, "
        f"expected tool used {_pct(s['expected_tool_pct'])}, "
        f"citations (sources and definition) {_pct(s['citation_pct'])}, "
        f"median latency {s['median_s']:.2f} s, "
        f"unsupported claims that reached a verified answer: {s['shipped_unsupported']}.",
        "",
        "## By category",
        "",
        "| Category | Questions | Passed | Numeric | Refusal |",
        "| --- | --- | --- | --- | --- |",
    ]  # fmt: skip
    for cat in CATEGORIES:
        rs = [x for x in results if x["category"] == cat]
        if not rs:
            continue
        num = [x["checks"]["numeric"] for x in rs if x["checks"]["numeric"] is not None]
        ref = [x["checks"]["refusal"] for x in rs if x["checks"]["refusal"] is not None]
        out.append(
            f"| {cat} | {len(rs)} | {sum(x['passed'] for x in rs)} | "
            f"{sum(bool(v) for v in num)}/{len(num)} | {sum(bool(v) for v in ref)}/{len(ref)} |"
        )
    out += ["", "## Failures", ""]
    if s["skipped"]:
        out += [f"Not run (cost or question limit): {', '.join(s['skipped'])}", ""]
    failed = [x for x in results if not x["passed"]]
    if not failed:
        out += ["None.", ""]
    for x in failed:
        bad = [k for k, v in x["checks"].items() if v is False]
        out += [
            f"### {x['id']} ({x['category']})",
            "",
            f"- **Question:** {x['question']}",
            f"- **Expected:** {x['behaviour']}, gold values {x['gold']['values']}"
            f"{' (withheld)' if x['gold']['withheld'] else ''}, mentions {x['gold']['mentions']}",
            f"- **Failed checks:** {', '.join(bad)}",
            f"- **Status:** {x['status']}",
            f"- **Shown text:** {x['text']!r}",
            f"- **Draft of the model:** {x['draft']!r}",
            f"- **Problems:** {x['problems']}",
            f"- **Tools called:** {x['tools_called']}",
            "",
            "Tool results:",
            "",
            "```json",
            json.dumps(x["results"], indent=1, default=str),
            "```",
            "",
        ]
    return "\n".join(out)
