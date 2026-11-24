"""Glue of the AI analyst page: key, client, budget and the view of an answer.

The tools, the orchestration and the checks live in `headway_mcp`; this module only wires them to
Streamlit. The OpenAI key comes from the Streamlit secret OPENAI_API_KEY; without it the page
shows the precomputed examples only.
"""

import sys
from pathlib import Path
from typing import Any

import streamlit as st

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "src", _ROOT / "dbt"):  # a checkout without the package installed
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from headway_mcp import budget, examples  # noqa: E402
from headway_mcp.analyst import Analyst, Answer  # noqa: E402
from headway_mcp.client import InProcessMcp  # noqa: E402
from headway_mcp.llm import ChatClient, OpenAIChat  # noqa: E402
from headway_mcp.tools import Tools  # noqa: E402

SECRET = "OPENAI_API_KEY"
STATUS_TEXT = {
    "verified": ":green[verified: every number, comparison and direction is in the tool results]",
    "could_not_verify": ":orange[could not verify]",
    "insufficient_data": ":orange[insufficient data]",
    "refused": ":red[refused]",
    "unavailable": ":red[unavailable]",
}


def api_key() -> str:
    """The OpenAI key from the Streamlit secrets, or "" when there is none."""
    try:
        return str(st.secrets.get(SECRET, "") or "").strip()
    except Exception:  # no secrets file
        return ""


def make_client(key: str) -> ChatClient:
    return OpenAIChat(key)


@st.cache_resource
def daily_budget() -> budget.DailyBudget:
    return budget.DailyBudget()


def session_limit() -> budget.SessionLimit:
    if "analyst_session" not in st.session_state:
        st.session_state["analyst_session"] = budget.SessionLimit()
    limit: budget.SessionLimit = st.session_state["analyst_session"]
    return limit


def ask(base: str, key: str, question: str) -> Answer:
    """One question through the in-process MCP client and server."""
    tools = Tools(base)
    return Analyst(tools, make_client(key), InProcessMcp(tools)).ask(question)


def example_list(doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    return examples.load(doc)


def provenance_rows(result: dict[str, Any]) -> dict[str, str]:
    """The provenance of one tool result as label -> text for the page."""
    p = result["provenance"]
    period = p.get("comparison_period", {})
    period_text = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in period.items())
    cov = p.get("coverage_pct")
    fresh = p.get("freshness", {})
    return {
        "Metric definition": f"{p.get('definition')} ({p['metrics_md']})",
        "Comparison period": period_text,
        "Coverage": "not applicable" if cov is None else f"{cov:g}% average arrival coverage",
        "Freshness": f"exported at {fresh.get('exported_at')}; metrics as of "
        f"{fresh.get('metrics_as_of')}",
        "Tables": ", ".join(p["tables"]),
    }
