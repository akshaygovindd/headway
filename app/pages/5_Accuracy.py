"""Accuracy: how close the feed's predictions are to reconstructed arrivals, and how far trains
ran from the timetable. Release 2; hidden when the export has no Release 2 tables."""

import plotly.graph_objects as go
import streamlit as st

import ui
from lib import accuracy
from lib.coverage import coverage_badge
from lib.lines import line_color

ui.page_header("Accuracy")
base = ui.data_base()
manifest = ui.gold_manifest(base)
ui.show_as_of_and_freshness(manifest, ui.live_snapshot(base))

acc = ui.gold_table_optional(base, "agg_prediction_accuracy_daily")
dev = ui.gold_table_optional(base, "agg_actual_deviation_hourly")
recon = ui.gold_table_optional(base, "agg_arrival_reconstruction_daily")
if acc is None or dev is None:
    st.info(
        "Accuracy metrics are not enabled in this deployment yet. They need the Release 2 "
        "tables (reconstructed arrivals), which this export does not contain."
    )
    st.stop()

st.caption(accuracy.arrival_coverage_note(recon))
st.caption(
    "Prediction accuracy compares the feed's predicted arrival with the reconstructed arrival. "
    "Hours with a data outage are left out; hours with an active detour, suspension or shuttle "
    "alert are kept and counted as service disruption."
)

st.subheader("Prediction accuracy by horizon")
st.caption(
    "Last prediction in each bucket of lead time before the arrival; error = predicted minus reconstructed."
)
table = accuracy.accuracy_by_horizon(acc)
if table.empty:
    st.info("No accuracy data in the exported window yet.")
else:
    lines = sorted(table["line"].unique())
    fig = go.Figure()
    for line in lines:
        sel = table[table["line"] == line]
        vals = [
            accuracy.shown(v, c)
            for v, c in zip(sel["median_abs_error_s"], sel["coverage_pct"], strict=True)
        ]
        fig.add_scatter(
            x=sel["horizon_bucket"].astype(str),
            y=vals,
            name=line,
            mode="lines+markers",
            line={"color": line_color(line)},
            customdata=sel[["n", "coverage_pct"]].to_numpy(),
            hovertemplate="%{x} min ahead<br>Median abs error %{y:.0f} s<br>n=%{customdata[0]}, coverage %{customdata[1]:.0f}%<extra>"
            + line
            + "</extra>",
        )
    fig.update_layout(xaxis={"title": "Minutes before arrival", "categoryorder": "array", "categoryarray": accuracy.BUCKETS},
                      yaxis_title="Median absolute error (s)", margin={"l": 10, "r": 10, "t": 10, "b": 10})  # fmt: skip
    st.plotly_chart(fig, width="stretch")
    view = table.assign(
        **{
            "Median abs error": [
                ui.fmt_seconds(accuracy.shown(v, c), signed=False)
                for v, c in zip(table["median_abs_error_s"], table["coverage_pct"], strict=True)
            ],
            "Mean abs error": [
                ui.fmt_seconds(accuracy.shown(v, c), signed=False)
                for v, c in zip(table["mae_s"], table["coverage_pct"], strict=True)
            ],
            "Within ±60 s": [
                ui.fmt_pct(accuracy.shown(v, c))
                for v, c in zip(table["pct_within_60s"], table["coverage_pct"], strict=True)
            ],
            "Within ±120 s": [
                ui.fmt_pct(accuracy.shown(v, c))
                for v, c in zip(table["pct_within_120s"], table["coverage_pct"], strict=True)
            ],
            "Coverage": [ui.fmt_pct(c) for c in table["coverage_pct"]],
        }
    ).rename(
        columns={
            "line": "Line",
            "horizon_bucket": "Minutes ahead",
            "n": "n",
            "n_disruption": "of which service disruption",
        }
    )
    st.dataframe(
        view[["Line", "Minutes ahead", "Median abs error", "Mean abs error", "Within ±60 s",
              "Within ±120 s", "Coverage", "n", "of which service disruption"]],
        hide_index=True,
        width="stretch",
    )  # fmt: skip
    st.caption(
        "The median is the n-weighted mean of daily medians. Values below 50% coverage are withheld."
    )

st.subheader("Actual deviation from the timetable")
st.caption(
    "Reconstructed arrival minus scheduled arrival; positive is late. Share on time = from 1 minute early to 5 minutes late."
)
summary = accuracy.actual_deviation_summary(dev)
if summary.empty:
    st.info("No actual-deviation data in the exported window yet.")
else:
    for _, row in summary.sort_values("line").iterrows():
        cov = row["coverage_pct"]
        badge = coverage_badge(cov)
        with st.container(border=True):
            head, tag = st.columns([2, 3])
            head.markdown(f"**{row['line']}**")
            tag.markdown(ui.badge_markdown(badge))
            if not badge.rankable:
                st.write("Not enough data to report values.")
            else:
                a, b, c = st.columns(3)
                a.metric("Median deviation", ui.fmt_seconds(row["median_deviation_s"]))
                b.metric("90th percentile", ui.fmt_seconds(row["p90_deviation_s"]))
                c.metric("On time (−1/+5 min)", ui.fmt_pct(row["pct_on_time_window"]))
            extra = (
                f", {int(row['n_excluded_outage'])} arrivals left out for data outages"
                if row["n_excluded_outage"]
                else ""
            )
            disruption = (
                f", {int(row['n_disruption_hours'])} service-disruption hours included"
                if row["n_disruption_hours"]
                else ""
            )
            st.caption(f"{ui.coverage_text(cov, row['n'])}{extra}{disruption}")
st.caption(ui.DISCLAIMER)
