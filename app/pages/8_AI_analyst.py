"""AI analyst: questions answered from five read-only tools over the public gold tables. A language
model writes the answer; it is shown only when every number, causal phrase and direction word is
checked against the tool results. Without an OpenAI key, over budget, or over the session limit, the
page shows precomputed example answers (no model at view time)."""

from typing import Any

import streamlit as st

import ui
from lib import analyst
from lib import data as data_lib

ui.page_header("AI analyst")
base = ui.data_base()
manifest = ui.gold_manifest(base)
ui.show_as_of_and_freshness(manifest, ui.live_snapshot(base))

st.caption(
    "Ask about line reliability. The analyst can use five read-only tools over Headway's public "
    "gold tables and nothing else. Every answer shows its SQL, metric definition, comparison "
    "period, coverage and freshness. It never gives causes: for 'why' questions it shows what the "
    "data shows and which service alerts were active. " + ui.DISCLAIMER
)


@st.cache_data(ttl=300, show_spinner=False)
def _examples(url: str) -> list[dict[str, Any]]:
    return analyst.example_list(data_lib.load_analyst_examples(url))


def show_results(results: list[dict[str, Any]]) -> None:
    for res in results:
        if res.get("status") != "ok":
            st.caption(f"Tool result: {res.get('status')}: {res.get('error') or res.get('reason')}")
            continue
        for label, text in analyst.provenance_rows(res).items():
            st.markdown(f"**{label}:** {text}")
        with st.expander("Sources: SQL and rows"):
            st.code(res["provenance"]["sql"], language="sql")
            st.dataframe(res["rows"], hide_index=True, width="stretch")


def show_examples(items: list[dict[str, Any]]) -> None:
    if not items:
        st.info("No example answers are published for this export yet.")
        return
    st.subheader("Example questions")
    st.caption("Answers precomputed from the same tools when the data was exported; no model runs.")
    for item in items:
        with st.container(border=True):
            st.markdown(f"**{item['question']}**")
            st.write(item["answer"])
            st.markdown(":green[precomputed from the tools]")
            show_results(item["results"])


key = analyst.api_key()
items = _examples(base)

if not key:
    st.info(
        "Live questions are off in this deployment (no OpenAI key is configured). The examples "
        "below were computed from the data without a model."
    )
    show_examples(items)
    st.stop()

question = st.text_input("Your question", max_chars=300, key="analyst_question")
if st.button("Ask", disabled=not question.strip()):
    verdict = analyst.budget.admit(analyst.daily_budget(), analyst.session_limit())
    if verdict != "ok":
        why = (
            "this session has used its questions"
            if verdict == "session"
            else ("today's question budget is spent")
        )
        st.warning(f"No live answer: {why}. Showing the examples instead.")
        show_examples(items)
        st.stop()
    with st.spinner("Asking the tools..."):
        answer = analyst.ask(base, key, question)
    st.markdown(analyst.STATUS_TEXT[answer.status])
    st.write(answer.text)
    for problem in answer.problems:
        st.caption(f"Check: {problem}")
    show_results(answer.results)
else:
    show_examples(items)
st.caption(ui.DISCLAIMER)
