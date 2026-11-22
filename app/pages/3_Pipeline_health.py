"""Pipeline health: quality checks, captures, duplicates, quarantine."""

import plotly.graph_objects as go
import streamlit as st

import ui
from lib import health

ui.page_header("Pipeline health")
base = ui.data_base()
manifest = ui.gold_manifest(base)
ui.show_as_of_and_freshness(manifest, ui.live_snapshot(base))

checks = ui.gold_table(base, "agg_quality_checks_hourly")
pipeline = ui.gold_table(base, "agg_pipeline_health")

ICONS = {
    "ok": "✅ ok",
    "warn": "⚠️ warn",
    "fail": "❌ fail",
    "no_baseline": "➖ no_baseline",
    "no_data": "➖ no_data",
}

st.subheader("Quality checks")
summary = health.check_summary(checks)
st.caption(" · ".join(f"{status}: {n}" for status, n in summary.items() if n))
grid = health.check_status_grid(checks)
st.dataframe(grid.apply(lambda col: col.map(lambda s: ICONS.get(s, str(s)))), width="stretch")

st.subheader("Captures per hour")
captures = health.captures_per_hour(pipeline)
if captures.empty:
    st.info("No ingest runs in the exported window.")
else:
    fig = go.Figure()
    fig.add_bar(x=captures["label"], y=captures["runs"], name="Runs")
    fig.add_bar(x=captures["label"], y=captures["missed"], name="Missed (gap)")
    fig.update_layout(
        barmode="stack", xaxis={"type": "category"}, margin={"l": 10, "r": 10, "t": 10, "b": 10}
    )
    st.plotly_chart(fig, width="stretch")
    gaps = captures[captures["missed"] > 0]
    st.caption(
        "No gaps in the window."
        if gaps.empty
        else f"{int(gaps['missed'].sum())} missed runs in {len(gaps)} hour(s)."
    )

left, right = st.columns(2)
with left:
    st.subheader("Duplicates")
    st.dataframe(health.duplicates_by_feed(pipeline), hide_index=True, width="stretch")
with right:
    st.subheader("Quarantine by reason")
    st.dataframe(health.quarantine_by_reason(pipeline), hide_index=True, width="stretch")

st.subheader("Late-event share")
late = health.check_series(checks, "late_event_share")
if late.empty:
    st.info("No late-event data yet.")
else:
    fig = go.Figure(go.Scatter(x=late["label"], y=late["value"], mode="lines+markers"))
    fig.update_layout(xaxis={"type": "category"}, yaxis_title="% of records later than 5 min",
                      margin={"l": 10, "r": 10, "t": 10, "b": 10})  # fmt: skip
    st.plotly_chart(fig, width="stretch")

st.subheader("Cost")
st.info("Cost metrics: available after Phase 5b.")
