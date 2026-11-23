"""Pipeline health: quality checks, captures, duplicates, quarantine."""

import plotly.graph_objects as go
import streamlit as st

import ui
from lib import data, health

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
st.dataframe(
    grid.apply(lambda col: col.map(lambda s: ICONS.get(s, "—") if isinstance(s, str) else "—")),
    width="stretch",
    column_config={
        c: st.column_config.TextColumn(c, help="— means no result for this check in that hour.")
        for c in grid.columns
    },
)

st.subheader("Captures per hour")
captures = health.captures_per_hour(pipeline)
if captures.empty:
    st.info("No ingest runs in the exported window.")
else:
    fig = go.Figure()
    labels = [
        f"{lbl} (partial)" if part else lbl
        for lbl, part in zip(captures["label"], captures["partial"], strict=True)
    ]
    fig.add_bar(x=labels, y=captures["runs"], name="Runs")
    fig.add_bar(x=labels, y=captures["missed"], name="Missed (gap)")
    fig.update_layout(
        barmode="stack", xaxis={"type": "category"}, margin={"l": 10, "r": 10, "t": 10, "b": 10}
    )
    st.plotly_chart(fig, width="stretch")
    gaps = captures[captures["missed"] > 0]
    missed = int(gaps["missed"].sum())
    hours_n = len(gaps)
    st.caption(
        "No gaps in the window."
        if gaps.empty
        else f"{missed} missed run{'s' if missed != 1 else ''} in "
        f"{hours_n} hour{'s' if hours_n != 1 else ''}."
    )
    if captures["partial"].any():
        st.caption("The hour marked (partial) is still in progress; its run count is not final.")

left, right = st.columns(2)
with left:
    st.subheader("Duplicates")
    st.dataframe(health.duplicates_by_feed(pipeline), hide_index=True, width="stretch")
with right:
    st.subheader("Quarantine by reason")
    reasons = health.quarantine_by_reason(pipeline)
    if reasons.empty:
        st.info("This gold export has no quarantine data.")
    else:
        st.dataframe(reasons, hide_index=True, width="stretch")
        if (reasons["reason"] == "not itemised in this export").any():
            st.caption("This gold export does not break every quarantined record down by reason.")

st.subheader("Late-event share")
late = health.check_series(checks, "late_event_share")
if late.empty or not ui.has_values(late["value"]):
    st.info("No late-event data yet.")
else:
    fig = go.Figure(go.Scatter(x=late["label"], y=late["value"], mode="lines+markers"))
    fig.update_layout(xaxis={"type": "category", "tickangle": -45}, yaxis_title="% of records later than 5 min",
                      margin={"l": 10, "r": 10, "t": 10, "b": 10})  # fmt: skip
    st.plotly_chart(fig, width="stretch")

slo_daily = ui.gold_table_optional(base, "agg_slo_daily")
slo_rollup = ui.gold_table_optional(base, "agg_slo_rollup")
if slo_daily is not None and slo_rollup is not None and not slo_daily.empty:
    st.subheader("Service level objectives")
    st.caption(
        "Share of good events against the target; budget is the part of the error budget the window "
        "has used (above 100% is overspent). The latest date may be partial. Lines under an active "
        "service disruption are reported on their own row and do not count toward arrival coverage. "
        "Definitions in docs/SLO.md."
    )
    st.dataframe(health.slo_table(slo_daily, slo_rollup), hide_index=True, width="stretch")

st.subheader("Cost")
try:
    cost = ui.gold_table(base, "cost_daily")
except data.DataUnavailableError:
    cost = None
if cost is None or cost.empty:
    st.info("No cost data yet.")
else:
    spend = health.cost_summary(cost)
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Spend on {spend['latest_date']}", f"${spend['latest_total_usd']:.2f}")
    c2.metric("Last 7 days", f"${spend['last_7_days_usd']:.2f}")
    per_1000 = spend["cost_per_1000_captures"]
    c3.metric(
        "Per 1,000 captures",
        "—" if per_1000 is None else f"${per_1000:.4f}",
        help="— means no capture count was available for that day.",
    )
    st.dataframe(spend["by_service"], hide_index=True, width="stretch")
