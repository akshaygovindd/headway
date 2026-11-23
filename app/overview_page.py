"""Overview: one card per line, ranked only where coverage is comparable."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from lib import accuracy
from lib.lines import line_color
from lib.overview import line_summary
from lib.ranking import rank_lines

ui.page_header("Overview")
base = ui.data_base()
manifest = ui.gold_manifest(base)
snapshot = ui.live_snapshot(base)
ui.show_as_of_and_freshness(manifest, snapshot)

summary = line_summary(ui.gold_table(base, "agg_line_reliability_window"))
if summary.empty:
    st.info("No reliability data in the exported window yet.")
    st.stop()

ranking = rank_lines([(r.line, r.median_deviation_s, r.coverage_pct) for r in summary.itertuples()])
order = [r.line for r in ranking.lines]
by_line = summary.set_index("line")

st.subheader("Predicted deviation by line")
ranked = [r for r in ranking.lines if r.rank is not None and pd.notna(r.value)]
if ranked and ui.has_values([r.value for r in ranked]):
    fig = go.Figure(
        go.Bar(
            y=[r.line for r in ranked],
            x=[r.value for r in ranked],
            orientation="h",
            marker_color=[line_color(r.line) for r in ranked],
            customdata=[r.coverage_pct for r in ranked],
            hovertemplate="%{y}: %{x:+.0f} s<br>Coverage %{customdata:.0f}%<extra></extra>",
        )
    )
    # Coverage labels sit in a fixed column at the right edge of the plot so no bar can clip them.
    fig.update_layout(
        annotations=[
            {
                "x": 1,
                "xref": "paper",
                "xanchor": "left",
                "y": r.line,
                "yref": "y",
                "text": f"{r.coverage_pct:.0f}% cov.",
                "showarrow": False,
            }
            for r in ranked
        ],
        xaxis_title="Median predicted deviation (s, positive is late)",
        yaxis={"autorange": "reversed", "type": "category"},
        margin={"l": 10, "r": 80, "t": 10, "b": 10},
        height=60 + 40 * len(ranked),
    )
    st.plotly_chart(fig, width="stretch")
for note in ranking.notes:
    st.caption(note)

disrupted = accuracy.disrupted_lines(
    ui.gold_table_optional(base, "agg_active_alerts"), manifest.get("metrics_as_of"), "last_24h"
)
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
            freshness=None,  # shown once at the top of the page
            disruption=accuracy.disruption_label(disrupted[line]) if line in disrupted else None,
        )
