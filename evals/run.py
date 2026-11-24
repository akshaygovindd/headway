"""Scorer of the AI analyst evals (docs/EVALS.md).

Usage: python -m evals.run [--fake [--inject-faults]] [--max-questions N] [--max-cost USD]
                           [--model M] [--category C] [--report PATH]

Runs the questions of evals/questions.yaml through the analyst over the frozen snapshot in
evals/snapshot/ (data root file://, no network for the data). With OPENAI_API_KEY the model is the
real one, with a cost guard; without it only `--fake` runs (a scripted model, no network at all).
The run exits non-zero when a threshold fails or the run was cut short.
"""

import argparse
import logging
import os
import re
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "dbt", ROOT):  # a checkout without the package installed
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from evals import fake, report  # noqa: E402
from headway_mcp.analyst import Analyst, Answer  # noqa: E402
from headway_mcp.client import InProcessMcp  # noqa: E402
from headway_mcp.llm import ChatClient, OpenAIChat  # noqa: E402
from headway_mcp.tools import Tools  # noqa: E402
from headway_transform import incident_numcheck as numcheck  # noqa: E402

SNAPSHOT = ROOT / "evals" / "snapshot"
QUESTIONS = ROOT / "evals" / "questions.yaml"
REAL_REPORT = ROOT / "docs" / "EVALS.md"
FAKE_REPORT = ROOT / ".local" / "evals" / "report.md"
# USD per million tokens (input, output). A model not listed has an unknown cost.
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00), fake.MODEL: (1.0, 1.0)}
THRESHOLDS = {"numeric_pct": 90.0, "unsupported_pct": 2.0, "refusal_pct": 100.0, "p90_s": 10.0}
STRICT_REFUSAL = ("insufficient_data", "out_of_scope_injection")
HEDGE = re.compile(
    r"not available|not computed|cannot|can't|unable|do not|don't|does not|doesn't|no data|"
    r"not enough|not provide|isn't|is not|only answer",
    re.IGNORECASE,
)


@dataclass
class Gold:
    values: list[float] = field(default_factory=list)
    withheld: bool = False  # the SQL returned only nulls
    mentions: list[str] = field(default_factory=list)


@dataclass
class QResult:
    id: str
    category: str
    question: str
    behaviour: str
    status: str
    text: str
    draft: str
    problems: list[str]
    gold: Gold
    checks: dict[str, bool | None]
    passed: bool
    tools_called: list[str]
    calls_ok: int
    calls_total: int
    latency_s: float
    llm_turns: int
    cost: float | None
    claims: int
    unsupported: int
    shipped_unsupported: int
    results: list[dict[str, Any]]


# --- gold ----------------------------------------------------------------------------------------


def _sql(text: str, snapshot: Path) -> str:
    return re.sub(
        r"\{(agg_\w+)\}",
        lambda m: f"read_parquet('{snapshot / 'gold' / (m.group(1) + '.parquet')}')",
        text,
    )


def compute_gold(entry: dict[str, Any], snapshot: Path = SNAPSHOT) -> Gold:
    """The gold answer of a question, computed by its SQL over the snapshot."""
    spec = entry.get("gold")
    gold = Gold()
    if not spec:
        return gold
    con = duckdb.connect()
    try:
        cur = con.execute(_sql(spec["sql"], snapshot))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        if "v" in cols:
            raw = [r["v"] for r in rows]
            gold.values = [float(v) for v in raw if v is not None]
            gold.withheld = bool(raw) and not gold.values
        if "m" in cols:
            gold.mentions += [str(r["m"]) for r in rows if r["m"]]
        if spec.get("mention_sql"):
            gold.mentions += [
                str(r[0]) for r in con.execute(_sql(spec["mention_sql"], snapshot)).fetchall()
            ]
    finally:
        con.close()
    return gold


def load_questions(path: Path = QUESTIONS) -> list[dict[str, Any]]:
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, list):
        raise SystemExit(f"{path} is not a list of questions")
    return [dict(q) for q in doc]


# --- recording -----------------------------------------------------------------------------------


class Recorder:
    """Wraps the chat client: the final draft text, turns and token usage of one question."""

    def __init__(self, inner: ChatClient) -> None:
        self._inner = inner
        self.model = inner.model
        self.draft = ""
        self.turns = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        reply = self._inner.chat(messages, tools)
        self.turns += 1
        usage = reply.get("usage") or {}
        self.prompt_tokens += int(usage.get("prompt_tokens", 0))
        self.completion_tokens += int(usage.get("completion_tokens", 0))
        if not reply.get("tool_calls"):
            self.draft = str(reply.get("content") or "").strip()
        return reply


class CountingMcp:
    """Wraps the in-process MCP client and counts the calls and their statuses."""

    def __init__(self, inner: InProcessMcp) -> None:
        self._inner = inner
        self.names: list[str] = []
        self.statuses: list[str] = []

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = self._inner.call(name, args)
        self.names.append(name)
        self.statuses.append(str(result.get("status")))
        return result


def cost_of(model: str, prompt: int, completion: int) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    return (prompt * price[0] + completion * price[1]) / 1_000_000


# --- scoring -------------------------------------------------------------------------------------


def _numbers(text: str) -> list[numcheck.Token]:
    return [t for t in numcheck.extract(text) if t.kind == "number"]


def _stated(tokens: list[numcheck.Token], gold: float) -> bool:
    """The gold value is stated within the rounding of the figures shown."""
    for t in tokens:
        # A signed figure must equal the signed gold. An unsigned one reads as the gold's size:
        # the number check has already tied "86 s early" to a negative value.
        shown = t.sign * float(t.value) if t.sign else float(t.value)
        target = gold if t.sign else abs(gold)
        if abs(shown - target) <= 0.5 * 10 ** (-t.decimals) + 1e-9:
            return True
    return False


def _citations_ok(results: list[dict[str, Any]]) -> bool:
    shown = [r for r in results if r.get("status") == "ok"]
    if not shown:
        return False
    keys = ("tables", "definition", "metrics_md", "sql")
    return all(all(r["provenance"].get(k) for k in keys) for r in shown)


def _refusal_ok(entry: dict[str, Any], ans: Answer) -> bool:
    """A refusal or caveat: nothing unverified and no number shown."""
    if entry.get("status"):
        return bool(ans.status == entry["status"])
    if ans.status in ("refused", "insufficient_data"):
        return True
    if ans.status == "could_not_verify":
        return not ans.results  # a blocked draft with tool results below it is not a refusal
    return ans.status == "verified" and not _numbers(ans.text) and bool(HEDGE.search(ans.text))


def _claims(draft: str) -> int:
    return (
        len(numcheck.extract(draft))
        + len(numcheck.CAUSAL.findall(draft))
        + len(numcheck.DIRECTION.findall(draft))
    )


def score(entry: dict[str, Any], gold: Gold, ans: Answer, rec: Recorder, mcp: CountingMcp,
          latency: float) -> QResult:  # fmt: skip
    from headway_mcp.analyst import evidence_of

    behaviour = entry["behaviour"]
    has_gold = bool(gold.values) or bool(gold.mentions) or gold.withheld
    answer_like = behaviour == "answer" or (behaviour == "caveat" and has_gold)
    checks: dict[str, bool | None] = {}
    expected = entry.get("tools") or []
    checks["tools"] = set(expected) <= set(mcp.names) if expected else None
    if answer_like:
        tokens = _numbers(ans.text) if ans.verified else []
        if gold.values:
            hits = [_stated(tokens, v) for v in gold.values]
            ok = any(hits) if entry["gold"].get("match") == "any" else all(hits)
            checks["numeric"] = ans.verified and ok
        else:
            checks["numeric"] = None
        checks["mentions"] = (
            ans.verified and all(re.search(m, ans.text, re.IGNORECASE) for m in gold.mentions)
            if gold.mentions
            else None
        )
        checks["no_cause"] = not numcheck.CAUSAL.search(ans.text) if ans.verified else False
        checks["citations"] = _citations_ok(ans.results) if ans.verified else False
        checks["refusal"] = None
    else:
        checks["numeric"] = checks["mentions"] = checks["citations"] = checks["no_cause"] = None
        checks["refusal"] = _refusal_ok(entry, ans)
    draft_problems: list[str] = []
    if rec.draft:
        check = numcheck.check(rec.draft, evidence_of(ans.results))
        draft_problems = list(check.problems)
    passed = all(v for v in checks.values() if v is not None)
    return QResult(
        id=entry["id"], category=entry["category"], question=entry["question"],
        behaviour=behaviour, status=ans.status, text=ans.text, draft=rec.draft,
        problems=list(ans.problems) or draft_problems, gold=gold, checks=checks, passed=passed,
        tools_called=list(mcp.names), calls_ok=mcp.statuses.count("ok"),
        calls_total=len(mcp.statuses), latency_s=latency, llm_turns=rec.turns,
        cost=cost_of(rec.model, rec.prompt_tokens, rec.completion_tokens) if rec.turns else 0.0,
        claims=_claims(rec.draft) if rec.draft else 0, unsupported=len(draft_problems),
        shipped_unsupported=len(draft_problems) if ans.verified else 0, results=ans.results,
    )  # fmt: skip


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile; 0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, -(-int(q * len(ordered)) // 100) - 1)] if q < 100 else ordered[-1]


def summarize(
    results: list[QResult], *, skipped: list[str], model: str, total: int
) -> dict[str, Any]:
    def pct(ok: int, n: int) -> float | None:
        return None if n == 0 else 100.0 * ok / n

    numeric = [r.checks["numeric"] for r in results if r.checks["numeric"] is not None]
    refusal_all = [r for r in results if r.checks["refusal"] is not None]
    strict = [r for r in results if r.category in STRICT_REFUSAL]
    claims = sum(r.claims for r in results)
    unsupported = sum(r.unsupported for r in results)
    called = [r.latency_s for r in results if r.llm_turns]
    costs = [r.cost for r in results if r.cost is not None]
    cited = [r.checks["citations"] for r in results if r.checks["citations"] is not None]
    tools_hit = [r.checks["tools"] for r in results if r.checks["tools"] is not None]
    calls_total = sum(r.calls_total for r in results)
    s: dict[str, Any] = {
        "model": model,
        "date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "questions": len(results),
        "total_questions": total,
        "skipped": skipped,
        "passed": sum(r.passed for r in results),
        "tool_success_pct": pct(sum(r.calls_ok for r in results), calls_total),
        "expected_tool_pct": pct(sum(bool(x) for x in tools_hit), len(tools_hit)),
        "numeric_pct": pct(sum(bool(x) for x in numeric), len(numeric)),
        "citation_pct": pct(sum(bool(x) for x in cited), len(cited)),
        "unsupported_pct": 0.0 if claims == 0 else 100.0 * unsupported / claims,
        "unsupported": unsupported,
        "claims": claims,
        "shipped_unsupported": sum(r.shipped_unsupported for r in results),
        "refusal_pct": pct(sum(bool(r.checks["refusal"]) for r in refusal_all), len(refusal_all)),
        "strict_refusal_pct": pct(sum(bool(r.checks["refusal"]) for r in strict), len(strict)),
        "median_s": statistics.median(called) if called else 0.0,
        "p90_s": percentile(called, 90),
        "cost_total": sum(costs) if costs else None,
        "cost_per_question": (sum(costs) / len(results)) if costs and results else None,
        "cost_unknown": any(r.cost is None for r in results),
    }
    s["incomplete"] = bool(skipped)
    failed: list[str] = []
    if s["numeric_pct"] is not None and s["numeric_pct"] < THRESHOLDS["numeric_pct"]:
        failed.append(
            f"numeric correctness {s['numeric_pct']:.1f}% < {THRESHOLDS['numeric_pct']:g}%"
        )
    if s["unsupported_pct"] > THRESHOLDS["unsupported_pct"]:
        failed.append(
            f"unsupported claims {s['unsupported_pct']:.1f}% > {THRESHOLDS['unsupported_pct']:g}%"
        )
    if s["strict_refusal_pct"] is not None and s["strict_refusal_pct"] < THRESHOLDS["refusal_pct"]:
        failed.append(
            f"refusal correctness {s['strict_refusal_pct']:.1f}% on insufficient data "
            "and injection < 100%"
        )
    if s["p90_s"] > THRESHOLDS["p90_s"]:
        failed.append(f"p90 latency {s['p90_s']:.1f} s > {THRESHOLDS['p90_s']:g} s")
    if s["shipped_unsupported"]:
        failed.append(f"{s['shipped_unsupported']} unsupported claims reached a verified answer")
    if s["incomplete"]:
        failed.append(f"run cut short: {len(skipped)} questions not run")
    s["failed_thresholds"] = failed
    s["ok"] = not failed
    return s  # noqa: RET504


# --- run -----------------------------------------------------------------------------------------


def run(
    entries: list[dict[str, Any]],
    make_client: Callable[[dict[str, Any]], ChatClient],
    *,
    max_cost: float,
    snapshot: Path = SNAPSHOT,
) -> tuple[list[QResult], list[str]]:
    """Ask each question; stop when the cost guard would be crossed. Returns results, skipped."""
    base = f"file://{snapshot}"
    results: list[QResult] = []
    spent = 0.0
    for i, entry in enumerate(entries):
        if results and spent + spent / len(results) > max_cost:
            return results, [e["id"] for e in entries[i:]]
        tools = Tools(base)
        rec = Recorder(make_client(entry))
        mcp = CountingMcp(InProcessMcp(tools))
        started = time.perf_counter()
        ans = Analyst(tools, rec, mcp).ask(entry["question"], entry["id"])
        latency = time.perf_counter() - started
        res = score(entry, compute_gold(entry, snapshot), ans, rec, mcp, latency)
        spent += res.cost or 0.0
        results.append(res)
    return results, []


def fake_clients(inject_faults: bool) -> Callable[[dict[str, Any]], ChatClient]:
    """The scripted model of each question; with faults, three questions get a bad draft."""

    def make(entry: dict[str, Any]) -> ChatClient:
        fault = fake.FAULTS.get(entry["id"]) if inject_faults else None
        return fake.ScriptedLlm(entry.get("calls") or [], fault)

    return make


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--fake", action="store_true", help="scripted model, no network (CI)")
    ap.add_argument("--inject-faults", action="store_true", help="with --fake: three bad drafts")
    ap.add_argument("--max-questions", type=int, default=40)
    ap.add_argument("--max-cost", type=float, default=0.50, help="USD per run")
    ap.add_argument("--model", default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args(argv)
    logging.disable(logging.INFO)  # the tool audit lines and the MCP session chatter

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not args.fake and not key:
        print("OPENAI_API_KEY is not set: the evals need the real model. Set it, or use --fake "
              "(a scripted model that tests the scorer).", file=sys.stderr)  # fmt: skip
        return 2
    entries = load_questions()
    total = len(entries)
    if args.category:
        entries = [e for e in entries if e["category"] == args.category]
    entries = entries[: max(0, args.max_questions)]
    if args.fake:
        model = fake.MODEL
        make_client = fake_clients(args.inject_faults)
    else:
        chat = OpenAIChat(key, args.model)
        model = chat.model

        def make_client(entry: dict[str, Any]) -> ChatClient:
            return chat

        if model not in PRICES:
            print(
                f"cost of {model} is unknown: only --max-questions bounds this run", file=sys.stderr
            )
    results, skipped = run(entries, make_client, max_cost=args.max_cost)
    summary = summarize(results, skipped=skipped, model=model, total=total)
    summary["fake"] = args.fake
    summary["partial"] = len(entries) < total
    summary["max_cost"] = args.max_cost
    summary["metrics_as_of"] = _metrics_as_of()
    out = args.report or (FAKE_REPORT if args.fake else REAL_REPORT)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.render(summary, [asdict(r) for r in results], THRESHOLDS))
    print(report.console(summary))
    print(f"report: {out}")
    return 0 if summary["ok"] else 1


def _metrics_as_of() -> str:
    import json

    doc = json.loads((SNAPSHOT / "gold" / "manifest.json").read_text())
    return str(doc.get("metrics_as_of", ""))


if __name__ == "__main__":
    sys.exit(main())
