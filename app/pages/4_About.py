"""About & methodology: what the numbers mean and where the details live."""

import streamlit as st

import ui

REPO = "https://github.com/akshaygovindd/headway"
DOCS = f"{REPO}/blob/main/docs"

ui.page_header("About & methodology")

st.markdown(
    "Headway measures near-real-time reliability of Boston subway lines from public MBTA "
    "GTFS and GTFS-realtime feeds. Every number is a Headway approximation, not an official "
    "MBTA metric, and is shown with its coverage and freshness."
)

st.subheader("How to read the numbers")
st.markdown(
    "- **Predicted deviation** compares the feed's last prediction before a scheduled arrival "
    "with the timetable. It is a prediction, not an observed arrival.\n"
    "- **Coverage** is the share of observable trip stops that have an eligible prediction. "
    "Below 50% values are withheld; lines are only ranked against each other where coverage is "
    "comparable.\n"
    "- **Freshness** is shown separately from the data period: *metrics as of* is the hour the "
    "metrics describe, *feed freshness* is how recently the gold tables and live snapshot were "
    "produced.\n"
    "- **Pipeline health** reports missed runs, duplicate deliveries and quarantined rows, so a "
    "gap in the data is not mistaken for a transit disruption."
)

st.subheader("Measured numbers")
st.markdown(
    "- Transform run: 600 s timeout before, about 46-54 s after the fix\n"
    "- Cost: $0.0086 per 1,000 captures\n"
    "- Reliability: 1 missed run in about 1,580 minutes\n"
    f"- Details and method: [MEASUREMENTS.md]({DOCS}/MEASUREMENTS.md)"
)

st.subheader("Documentation")
st.markdown(
    f"- [METRICS.md]({DOCS}/METRICS.md): definitions, grain and inclusion rules of the four "
    "Release 1 metrics\n"
    f"- [INCIDENTS.md]({DOCS}/INCIDENTS.md): production incidents and what now prevents them\n"
    f"- [MEASUREMENTS.md]({DOCS}/MEASUREMENTS.md): measured runtimes, volumes, reliability and cost\n"
    f"- [Source repository]({REPO})"
)
