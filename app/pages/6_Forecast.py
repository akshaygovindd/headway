"""Forecast: arrival-delay forecasts for the next hour against baselines and MBTA's own
prediction, and, only for lines past the training gate, the honest model comparison.
Release 3; hidden when the export has no forecast tables."""

import pandas as pd
import streamlit as st

import ui
from lib import forecast, weather
from lib.coverage import WITHHELD_TEXT, coverage_badge, is_withheld
from lib.timefmt import format_local

ui.page_header("Forecast")
base = ui.data_base()
manifest = ui.gold_manifest(base)
ui.show_as_of_and_freshness(manifest, ui.live_snapshot(base))

gate = ui.gold_table_optional(base, "agg_forecast_gate_status")
nh = ui.gold_table_optional(base, "agg_forecast_next_hour")
bt = ui.gold_table_optional(base, "agg_forecast_backtest")
if gate is None or nh is None:
    st.info(
        "Forecasts are not enabled in this deployment yet. They need the Release 3 tables "
        "(forecast serving and the training gate), which this export does not contain."
    )
    st.stop()

st.caption(
    "Forecasts of the delay (reconstructed arrival minus scheduled arrival, positive is late) of "
    "trip stops scheduled in the next hour, by minutes ahead. Errors in any comparison are "
    "measured against reconstructed arrivals, so they include the reconstruction error and cannot "
    "be smaller than it. MBTA's own prediction is shown as a reference, not as a baseline. Values "
    "from under 50% coverage are withheld."
)
if not nh.empty:
    t = nh["prediction_ts"].max()
    st.markdown(
        f"**Forecast made for:** {format_local(t)} (slots start 15, 30 and 60 minutes later)"
    )

lines = sorted(set(gate["line"]) | set(nh["line"]))
if not lines:
    st.info("No lines in the export yet.")
for line in lines:
    grow = gate[gate["line"] == line]
    status, reason = forecast.line_status(nh, line)
    with st.container(border=True):
        head, tag = st.columns([2, 3])
        head.subheader(line)
        tag.markdown(":green[model forecast]" if status == "model" else ":gray[baselines only]")
        if len(grow):
            st.caption(forecast.gate_line(grow.iloc[0]))
        if status != "model":
            st.caption(f"No model forecast: {reason}.")

        passed = bool(len(grow) and grow.iloc[0]["gate_passed"])
        if passed:
            for h in forecast.HORIZONS:
                cmp = forecast.comparison(bt, line, h)
                if cmp is None:
                    continue
                text = f"{h} min ahead: {cmp.text}"
                {"better": st.success, "worse": st.error}.get(cmp.verdict, st.info)(text)
            ctx = forecast.context_table(bt, line)
            if len(ctx):
                view = pd.DataFrame(
                    {
                        "Horizon": ctx["horizon_min"].astype(str) + " min",
                        "Method": ctx["method"].map(lambda m: forecast.LABELS.get(m, m)),
                        "Mean absolute error": [
                            ui.cell_seconds(v, c, signed=False)
                            for v, c in zip(ctx["mae"], ctx["coverage_pct"], strict=True)
                        ],
                        "95% interval": [
                            WITHHELD_TEXT if is_withheld(c) else f"{lo:.0f} to {hi:.0f} s"
                            for lo, hi, c in zip(
                                ctx["ci_lo"], ctx["ci_hi"], ctx["coverage_pct"], strict=True
                            )
                        ],
                        "Coverage": [ui.fmt_pct(c) for c in ctx["coverage_pct"]],
                        "n": ctx["n"],
                    }
                )
                st.markdown("**Backtest, pooled over the test weeks (strict view)**")
                st.dataframe(view, hide_index=True, width="stretch")
                st.caption(
                    "The comparison is the paired difference of the mean absolute error on the "
                    "same examples, with a 95% interval over prediction times; the interval of "
                    "each method alone is shown for context. Coverage is the examples scored over "
                    "the labelled examples."
                )
        elif len(grow):
            st.caption("No model comparison is shown for this line until the gate is passed.")

        table = forecast.next_hour_table(nh, line)
        if table.empty:
            st.write("No scheduled trip stops in the next hour.")
            continue
        cov = table["coverage_pct"]
        shown = pd.DataFrame(
            {
                "Horizon": table["horizon_min"].astype(str) + " min",
                "Method": table["method"].map(lambda m: forecast.LABELS.get(m, m)),
                "Forecast delay": [
                    ui.cell_seconds(v, c) for v, c in zip(table["value"], cov, strict=True)
                ],
                "Coverage": [ui.fmt_pct(c) for c in cov],
                "n": table["n"],
            }
        )
        st.markdown("**Next hour**")
        st.dataframe(shown, hide_index=True, width="stretch")
        age = forecast.feature_age_s(nh, line)
        badge = coverage_badge(float(cov.min())) if len(cov) else None
        st.caption(
            "Freshness: latest arrival known to the features "
            + ("n/a" if age is None else f"{age / 60:.0f} min before the prediction time")
            + (f" · lowest coverage: {badge.label}" if badge else "")
            + ". Coverage is the share of scheduled trip stops in the slot that have a value."
        )
wx = ui.gold_table_optional(base, "agg_weather_comparison")
if wx is not None and not wx.empty:
    st.subheader("Weather comparison")
    if weather.has_no_days(wx):
        st.caption("No daily weather in this export yet, so no weather comparison is shown.")
    else:
        window = weather.window_text(wx)
        if window:
            st.caption(window)
        for item in weather.statements(wx):
            st.markdown(f":gray[{item.text}]" if item.withheld else item.text)
        st.caption(weather.NOTE)

st.caption(ui.DISCLAIMER)
