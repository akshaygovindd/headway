"""Line explorer: deviation by hour and weekday, and the slowest stretches."""

import plotly.graph_objects as go
import streamlit as st

import ui
from lib import explorer
from lib.coverage import NORMAL_MIN
from lib.lines import line_color

ui.page_header("Line explorer")
base = ui.data_base()
manifest = ui.gold_manifest(base)
ui.show_as_of_and_freshness(manifest, ui.live_snapshot(base))

hourly = ui.gold_table(base, "agg_line_reliability_hourly")
stops = ui.gold_table(base, "agg_stop_deviation_daily")
if hourly.empty:
    st.info("No reliability data in the exported window yet.")
    st.stop()

left, right = st.columns(2)
line = left.selectbox("Line", sorted(hourly["line"].unique()))
dirs = explorer.directions(hourly, line)
labels = explorer.direction_labels(stops, ui.gold_table(base, "dim_stop"), line, dirs)
choice = right.selectbox(
    "Direction",
    [*dirs, "Both"],  # the first real direction is the default
    format_func=lambda d: d if d == "Both" else labels[int(d)],
)
direction = None if choice == "Both" else int(choice)

st.subheader("Median deviation by hour and weekday")
st.caption("Hours are bucketed by service day (the day runs 03:00 to 03:00).")
cells = explorer.heatmap(hourly, line, direction)
if cells.empty or not ui.has_values(cells["median_deviation_s"]):
    st.info("No data for this selection.")
else:
    dev = explorer.heatmap_matrix(cells, "median_deviation_s")
    cov = explorer.heatmap_matrix(cells, "coverage_pct")
    fig = go.Figure(
        go.Heatmap(
            z=dev.to_numpy(),
            x=list(dev.columns),
            y=list(dev.index),
            customdata=cov.to_numpy(),
            colorscale="RdBu_r",
            zmid=0,
            colorbar={"title": "s"},
            hovertemplate="%{x} %{y}<br>Median deviation %{z:+.0f} s<br>Coverage %{customdata:.0f}%<extra></extra>",
        )
    )
    fig.update_layout(yaxis={"type": "category", "autorange": "reversed"},
                      xaxis={"type": "category", "categoryorder": "array", "categoryarray": list(dev.columns)},
                      margin={"l": 10, "r": 10, "t": 10, "b": 10})  # fmt: skip
    st.plotly_chart(fig, width="stretch")

st.subheader("Deviation by hour")
series = explorer.deviation_by_hour(hourly, line, direction)
if not series.empty and ui.has_values(series["median_deviation_s"], series["p90_deviation_s"]):
    fig = go.Figure()
    fig.add_scatter(x=series["label"], y=series["median_deviation_s"], name="Median",
                    mode="lines+markers", line={"color": line_color(line)})  # fmt: skip
    fig.add_scatter(x=series["label"], y=series["p90_deviation_s"], name="p90",
                    mode="lines+markers", line={"color": line_color(line), "dash": "dot"})  # fmt: skip
    step = max(1, len(series) // 12)
    fig.update_layout(xaxis={"type": "category", "tickangle": -45, "tickmode": "array",
                             "tickvals": list(series["label"][::step])},
                      yaxis_title="Predicted deviation (s)",
                      margin={"l": 10, "r": 10, "t": 10, "b": 10})  # fmt: skip
    st.plotly_chart(fig, width="stretch")

st.subheader("Slowest stretches")
st.caption(
    f"Top 5 consecutive stop pairs with the largest positive rise in median deviation; both stops need at least {NORMAL_MIN:.0f}% coverage."
)
if direction is None:
    st.info("Choose a direction to see stretches.")
else:
    stretches = explorer.slowest_stretches(stops, ui.gold_table(base, "dim_stop"), line, direction)
    if stretches.empty:
        st.info("No stretch has enough coverage at both ends.")
    else:
        st.dataframe(
            stretches.rename(columns={
                "from_stop": "From", "to_stop": "To", "rise_s": "Rise (s)",
                "from_deviation_s": "Deviation at From (s)", "to_deviation_s": "Deviation at To (s)",
                "min_coverage_pct": "Lower coverage (%)",
            }),
            hide_index=True,
            width="stretch",
        )  # fmt: skip
