"""Accuracy: how close the feed's predictions are to reconstructed arrivals, and how far trains
ran from the timetable. Release 2; hidden when the export has no Release 2 tables."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from lib import accuracy, countdown
from lib.coverage import coverage_badge
from lib.lines import line_color

ui.page_header("Accuracy")
base = ui.data_base()
manifest = ui.gold_manifest(base)
ui.show_as_of_and_freshness(manifest, ui.live_snapshot(base))

acc = ui.gold_table_optional(base, "agg_prediction_accuracy_daily")
dev = ui.gold_table_optional(base, "agg_actual_deviation_hourly")
recon = ui.gold_table_optional(base, "agg_arrival_reconstruction_daily")
acc_win = ui.gold_table_optional(base, "agg_accuracy_window")
dev_win = ui.gold_table_optional(base, "agg_actual_deviation_window")
alerts = ui.gold_table_optional(base, "agg_active_alerts")
cd_win = ui.gold_table_optional(base, "agg_countdown_accuracy_window")
cd_daily = ui.gold_table_optional(base, "agg_countdown_accuracy_daily")
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

options = accuracy.window_options(acc_win, dev_win)
window = st.radio("Window", list(options), format_func=options.get, horizontal=True)
exact = window != accuracy.ALL_DAYS
disrupted = accuracy.disrupted_lines(alerts, manifest.get("metrics_as_of"), window)
if disrupted:
    st.caption(
        "Lines under a service disruption in this window: "
        + "; ".join(f"{ln} ({', '.join(fx)})" for ln, fx in sorted(disrupted.items()))
        + ". Their hours are kept in the figures and counted as service disruption."
    )

st.subheader("Prediction accuracy by horizon")
st.caption(
    "Last prediction in each bucket of lead time before the arrival; error = predicted minus reconstructed."
)
table = (
    accuracy.accuracy_window_by_horizon(acc_win, window)
    if exact
    else accuracy.accuracy_by_horizon(acc)
)
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
        if not ui.has_values(vals):
            continue  # every bucket of the line is withheld
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
    if fig.data:
        fig.update_layout(xaxis={"title": "Minutes before arrival", "categoryorder": "array", "categoryarray": accuracy.BUCKETS},
                          yaxis_title="Median absolute error (s)", margin={"l": 10, "r": 10, "t": 10, "b": 10})  # fmt: skip
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Every line is below 50% coverage in this window, so no accuracy values are shown.")
    st.markdown("**Prediction accuracy by line and horizon**")
    cov = table["coverage_pct"]
    view = pd.DataFrame(
        {
            "Line": table["line"],
            "Minutes ahead": table["horizon_bucket"].astype(str),
            "Median abs error": [ui.cell_seconds(v, c, signed=False) for v, c in zip(table["median_abs_error_s"], cov, strict=True)],
            "MAE": [ui.cell_seconds(v, c, signed=False) for v, c in zip(table["mae_s"], cov, strict=True)],
            "Within ±60 s": [ui.cell_pct(v, c) for v, c in zip(table["pct_within_60s"], cov, strict=True)],
            "Within ±120 s": [ui.cell_pct(v, c) for v, c in zip(table["pct_within_120s"], cov, strict=True)],
            "n": table["n"],
            "Coverage": [ui.fmt_pct(c) for c in cov],
            "of which service disruption": table["n_disruption"],
        }
    )  # fmt: skip
    st.dataframe(view, hide_index=True, width="stretch")
    st.caption(
        "Medians are computed over every error in the window. Coverage is the share of scheduled trip stops with a prediction in the bucket; values below 50% are withheld."
        if exact
        else "The median is the n-weighted mean of daily medians, an approximation. Coverage is the share of scheduled trip stops with a prediction in the bucket; values below 50% are withheld."
    )

st.subheader("Actual deviation from the timetable")
st.caption(
    "Reconstructed arrival minus scheduled arrival; positive is late. Share on time = from 1 minute early to 5 minutes late."
)
summary = (
    accuracy.deviation_window_summary(dev_win, window)
    if exact
    else accuracy.actual_deviation_summary(dev)
)
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
            if row["line"] in disrupted:
                st.markdown(f":orange[{accuracy.disruption_label(disrupted[row['line']])}]")
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
            n_disrupted = row["n_disruption"] if exact else row["n_disruption_hours"]
            unit = "arrivals in service-disruption hours" if exact else "service-disruption hours"
            disruption = f", {int(n_disrupted):,} {unit} included" if n_disrupted else ""
            st.caption(f"{ui.coverage_text(cov, row['n'])}{extra}{disruption}")

st.subheader("Countdown accuracy")
st.caption(
    f"{countdown.LABEL}. How often a 1, 3, 5 or 10 minute countdown was right: the share of "
    "countdowns after which the train arrived within a minute (or two) of the predicted time. A "
    "train that never came counts as a miss. Checked against reconstructed arrivals, so a "
    "Headway approximation."
)
cd = countdown.source(cd_win, cd_daily, window)
if cd.empty:
    st.info("Countdown accuracy is not available for this window in this export yet.")
else:
    for line in sorted(cd["line"].unique()):
        rows = countdown.by_line(cd, line)
        with st.container(border=True):
            st.markdown(f"**{line}**")
            cov = rows["coverage_pct"]
            view = pd.DataFrame(
                {
                    "Countdown": [f"{int(m)} min" for m in rows["countdown_min"]],
                    "Within ±60 s": [ui.cell_pct(v, c) for v, c in zip(rows["pct_within_60s"], cov, strict=True)],
                    "Within ±120 s": [ui.cell_pct(v, c) for v, c in zip(rows["pct_within_120s"], cov, strict=True)],
                    "n": rows["n"],
                    "Could not be checked": rows["n_unknown"],
                    "Coverage": [ui.fmt_pct(c) for c in cov],
                }
            )  # fmt: skip
            st.dataframe(view, hide_index=True, width="stretch")
            extra = (
                f", {int(rows['n_excluded_outage'].sum()):,} left out for data outages"
                if rows["n_excluded_outage"].sum()
                else ""
            )
            disrupted_n = int(rows["n_disruption"].sum())
            disruption = (
                f", {disrupted_n:,} in service-disruption hours (kept)" if disrupted_n else ""
            )
            st.caption(countdown.unchecked_note(rows) + extra + disruption)
            with st.expander(f"{line}: stations"):
                stations = countdown.by_station(cd, line)
                scov = stations["coverage_pct"]
                st.dataframe(
                    pd.DataFrame(
                        {
                            "Station": stations["station_name"],
                            "Countdown": [f"{int(m)} min" for m in stations["countdown_min"]],
                            "Within ±60 s": [
                                ui.cell_pct(v, c)
                                for v, c in zip(stations["pct_within_60s"], scov, strict=True)
                            ],
                            "Within ±120 s": [
                                ui.cell_pct(v, c)
                                for v, c in zip(stations["pct_within_120s"], scov, strict=True)
                            ],
                            "n": stations["n"],
                            "Could not be checked": stations["n_unknown"],
                            "Coverage": [ui.fmt_pct(c) for c in scov],
                        }
                    ),  # fmt: skip
                    hide_index=True,
                    width="stretch",
                )
                st.caption(
                    "Coverage is the share of the station's scheduled trip stops with a counted "
                    "countdown in the bucket; values below 50% are withheld."
                )
    st.caption(
        "Coverage is the share of scheduled trip stops with a counted countdown in the bucket; "
        "values below 50% are withheld. Lines and windows add up counts, so they are exact."
    )
st.caption(ui.DISCLAIMER)
