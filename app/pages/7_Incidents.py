"""Incidents: hours when a line's deviation, headway gaps, skipped stops or a service alert crossed
a threshold for at least two hours, with a summary that states only what the evidence shows, and
whether a line-scope service alert overlapped. Release 3; hidden when the export has no incident
tables."""

import pandas as pd
import streamlit as st

import ui
from lib import incidents
from lib.coverage import WITHHELD_TEXT, coverage_badge, is_withheld
from lib.timefmt import format_local

ui.page_header("Incidents")
base = ui.data_base()
manifest = ui.gold_manifest(base)
ui.show_as_of_and_freshness(manifest, ui.live_snapshot(base))

inc = ui.gold_table_optional(base, "agg_incidents")
daily = ui.gold_table_optional(base, "agg_alert_matching_daily")
if inc is None:
    st.info(
        "Incidents are not enabled in this deployment yet. They need the Release 3 tables, which "
        "this export does not contain."
    )
    st.stop()

st.caption(
    "An incident opens when, for at least two hours in a row, a line and direction cross a "
    "threshold: median deviation 180 s or more above the usual for the day type and hour, 30% or "
    "more headway gaps, 20% or more skipped stops, or an active line-wide service alert. It closes "
    "after one hour with none. Data-outage hours are not judged. Summaries are written from the "
    "evidence shown and checked against it: a number that is not in the evidence blocks the "
    "text, and a deterministic template is used instead."
)

totals = incidents.day_summary(daily)
if totals:
    cols = st.columns(4)
    cols[0].metric("Incidents", f"{totals['incidents']:.0f}")
    cols[1].metric("Matched with an alert", f"{totals['incidents_matched']:.0f}")
    cols[2].metric("Unmatched incidents", f"{totals['incidents_unmatched']:.0f}")
    cols[3].metric("Unmatched alerts", f"{totals['alerts_unmatched']:.0f}")
    st.caption(
        f"Of {totals['alerts']:.0f} line-wide service alerts in the same period, "
        f"{totals['alerts_unmatched']:.0f} had no incident within 30 minutes. "
        + incidents.HANDCHECK_NOTE
    )
else:
    st.caption(incidents.HANDCHECK_NOTE)

if inc.empty:
    st.info("No incident in the exported period.")
    st.caption(ui.DISCLAIMER)
    st.stop()

for _, row in incidents.incident_list(inc).head(50).iterrows():
    doc = incidents.evidence(row["evidence_json"])
    with st.container(border=True):
        head, tag = st.columns([3, 2])
        direction = (
            "" if pd.isna(row["direction_id"]) else f" · direction {int(row['direction_id'])}"
        )
        head.subheader(f"{row['line']}{direction}")
        badge = coverage_badge(row["min_coverage_pct"])
        tag.markdown(ui.badge_markdown(badge))
        end = format_local(row["incident_end_ts"])
        st.markdown(
            f"**{format_local(row['incident_start_ts'])} to {end}** "
            f"({int(row['n_hours'])} h" + (", still open" if row["is_open"] else "") + ")"
        )
        st.caption(f"Thresholds crossed: {incidents.rules_text(row['thresholds_crossed'])}")

        if not incidents.summary_shown(row):
            st.write(incidents.SUMMARY_WITHHELD)
        elif pd.isna(row["summary_text"]):
            st.write("Summary not generated yet.")
        else:
            st.write(row["summary_text"])
            if row["template_used"]:
                st.markdown(":orange[template used]")
                reason = row["rejection_reason"]
                if row["number_check"] == "rejected":
                    st.caption("The written summary failed the number check and was replaced.")
                elif isinstance(reason, str) and reason:
                    st.caption(f"Template: {reason.replace('_', ' ')}.")
            else:
                st.markdown(":green[written by a language model, number check passed]")
            if row["summary_stale"]:
                st.caption("This summary describes an earlier version of the open incident.")

        hours = incidents.evidence_hours(doc)
        cov = hours["coverage_pct"]
        shown = pd.DataFrame(
            {
                "Hour": hours["hour"],
                "Median deviation": [
                    ui.cell_seconds(v, c)
                    for v, c in zip(hours["median_deviation_s"], cov, strict=True)
                ],
                "Usual for day type and hour": [
                    ui.cell_seconds(v, c)
                    for v, c in zip(hours["baseline_median_s"], cov, strict=True)
                ],
                "Headway gaps": [
                    ui.cell_pct(v, c) for v, c in zip(hours["gap_share_pct"], cov, strict=True)
                ],
                "Skipped stops": [
                    ui.cell_pct(v, c) for v, c in zip(hours["skipped_share_pct"], cov, strict=True)
                ],
                "Coverage": [ui.fmt_pct(c) for c in cov],
                "Rules crossed": [
                    WITHHELD_TEXT if is_withheld(c) else r
                    for r, c in zip(hours["crossed"], cov, strict=True)
                ],
            }
        )
        with st.expander("Evidence"):
            st.dataframe(shown, hide_index=True, width="stretch")
            rule = doc.get("baseline_rule", "")
            st.caption(
                f"Baseline rule: {rule} ({doc.get('baseline_days', 0)} earlier service days of this "
                f"day type at most). Data-outage hours inside the window: "
                f"{int(doc.get('data_outage_hours_inside', 0))}."
            )
            for alert in doc.get("alerts", []):
                st.caption(
                    f"Alert {alert.get('alert_id')}: {alert.get('effect')}, {alert.get('header')}"
                )

        status = incidents.MATCH_LABELS.get(row["match_status"], str(row["match_status"]))
        st.markdown(f"**Alert match:** {status}")
        if row["match_status"] == "matched":
            st.caption(incidents.lag_text(row["alert_lag_s"]).capitalize() + ".")
        st.caption(incidents.HANDCHECK_NOTE)
st.caption(ui.DISCLAIMER)
