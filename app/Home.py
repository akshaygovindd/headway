"""Overview: one card per line, ranked only where coverage is comparable."""

import plotly.graph_objects as go
import streamlit as st

import ui
from lib.lines import line_color
from lib.overview import line_summary
from lib.ranking import rank_lines

ui.page_header("Overview")
base = ui.data_base()
manifest = ui.gold_manifest(base)
snapshot = ui.live_snapshot(base)
ui.show_as_of_and_freshness(manifest, snapshot)
freshness = ui.freshness_text(manifest, snapshot)

summary = line_summary(ui.gold_table(base, "agg_line_reliability_window"))
if summary.empty:
    st.info("No reliability data in the exported window yet.")
    st.stop()

ranking = rank_lines([(r.line, r.median_deviation_s, r.coverage_pct) for r in summary.itertuples()])
order = [r.line for r in ranking.lines]
by_line = summary.set_index("line")

st.subheader("Predicted deviation by line")
ranked = [r for r in ranking.lines if r.rank is not None]
if ranked:
    fig = go.Figure(
        go.Bar(
            y=[r.line for r in ranked],
            x=[r.value for r in ranked],
            orientation="h",
            marker_color=[line_color(r.line) for r in ranked],
            text=[f"{r.coverage_pct:.0f}% coverage" for r in ranked],
            textposition="outside",
            hovertemplate="%{y}: %{x:+.0f} s<extra></extra>",
        )
    )
    fig.update_layout(
        xaxis_title="Median predicted deviation (s, positive is late)",
        yaxis={"autorange": "reversed", "type": "category"},
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        height=60 + 40 * len(ranked),
    )
    st.plotly_chart(fig, width="stretch")
for note in ranking.notes:
    st.caption(note)

st.subheader("Lines")
cols = st.columns(2)
for i, line in enumerate(order):
    row = by_line.loc[line]
    with cols[i % 2]:
        ui.render_metric_card(
            line,
            {
                "Median predicted deviation": row["median_deviation_s"],
                "p90 predicted deviation": row["p90_deviation_s"],
                "Median revision": row["median_revision_s"],
                "Skipped share": row["skipped_share"],
            },
            coverage_pct=row["coverage_pct"],
            freshness=freshness,
        )
