"""Line explorer: headway adherence (Release 2, when exported), deviation by hour and weekday, and
the slowest stretches. Heavy rail runs to headways, so it shows headway adherence first."""

import plotly.graph_objects as go
import streamlit as st

import ui
from lib import accuracy, explorer
from lib.coverage import NORMAL_MIN, coverage_badge
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

headways = ui.gold_table_optional(base, "agg_headway_adherence_hourly")
ett = ui.gold_table_optional(base, "agg_ett_daily")
recon = ui.gold_table_optional(base, "agg_arrival_reconstruction_daily")


def headway_adherence() -> None:
    """Headway adherence and ETT; absent entirely when the export has no Release 2 tables."""
    if headways is None:
        return
    st.subheader("Headway adherence")
    st.caption(
        "Share of train-to-train gaps at the scheduled spacing: within 0.5 to 1.5 times the scheduled "
        "headway is on target, below 0.5 is bunching, above 1.5 is a gap."
    )
    st.caption(accuracy.arrival_coverage_note(recon, line))
    s = accuracy.headway_summary(headways, line, direction)
    badge = coverage_badge(s["coverage_pct"])
    cols = st.columns([2, 1, 1, 1])
    cols[0].markdown(ui.badge_markdown(badge))
    if not badge.rankable:
        st.write("Not enough data to report values.")
    else:
        cols[1].metric("Within 0.5-1.5×", ui.fmt_pct(s["pct_within_band"]))
        cols[2].metric("Bunched (< 0.5×)", ui.fmt_pct(s["pct_bunched"]))
        cols[3].metric("Gap (> 1.5×)", ui.fmt_pct(s["pct_gap"]))
    dropped = (
        f", {int(s['n_dropped']):,} headways dropped (a train could lie between, or order unproven)"
        if s["n_dropped"]
        else ""
    )
    outage = (
        f", {int(s['n_excluded_outage']):,} left out for data outages"
        if s["n_excluded_outage"]
        else ""
    )
    disruption = (
        f", {int(s['n_disruption_hours'])} service-disruption hours included"
        if s["n_disruption_hours"]
        else ""
    )
    st.caption(f"{ui.coverage_text(s['coverage_pct'], s['n'])}{dropped}{outage}{disruption}")
    by_hour = accuracy.headway_by_hour(headways, line, direction)
    if not by_hour.empty and ui.has_values(by_hour["pct_within_band"]) and badge.rankable:
        fig = go.Figure(go.Bar(
            x=[f"{h:02d}:00" for h in by_hour["hour_local"]],
            y=by_hour["pct_within_band"],
            marker_color=line_color(line),
            customdata=by_hour[["n", "coverage_pct"]].to_numpy(),
            hovertemplate="%{x}<br>Within band %{y:.0f}%<br>n=%{customdata[0]}, coverage %{customdata[1]:.0f}%<extra></extra>",
        ))  # fmt: skip
        fig.update_layout(yaxis_title="Within band (%)", xaxis={"type": "category"},
                          margin={"l": 10, "r": 10, "t": 10, "b": 10})  # fmt: skip
        st.plotly_chart(fig, width="stretch")
    if ett is not None:
        bands = accuracy.ett_by_band(ett, line, direction)
        st.markdown("**ETT approximation (weekdays)**")
        st.caption(
            "Second stop to second-to-last stop, minus the scheduled running time. Adapted approximation, not an official MBTA measure."
        )
        view = bands.assign(
            **{
                "Median ETT": [
                    ui.fmt_seconds(accuracy.shown(v, c))
                    for v, c in zip(bands["median_ett_s"], bands["coverage_pct"], strict=True)
                ],
                "90th percentile": [
                    ui.fmt_seconds(accuracy.shown(v, c))
                    for v, c in zip(bands["p90_ett_s"], bands["coverage_pct"], strict=True)
                ],
                "Coverage": [ui.fmt_pct(c) for c in bands["coverage_pct"]],
            }
        ).rename(columns={"time_band": "Time band", "n": "Trips"})
        st.dataframe(
            view[["Time band", "Median ETT", "90th percentile", "Coverage", "Trips"]],
            hide_index=True,
            width="stretch",
        )


def schedule_deviation() -> None:

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
        stretches = explorer.slowest_stretches(
            stops, ui.gold_table(base, "dim_stop"), line, direction
        )
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


# Heavy rail runs to headways: adherence first, schedule deviation second. Other lines keep the
# deviation first.
if accuracy.is_heavy_rail(line):
    headway_adherence()
    schedule_deviation()
else:
    schedule_deviation()
    headway_adherence()
