"""Entrypoint: navigation, so the first page is labelled "Overview" in the sidebar."""

import streamlit as st

pages = [
    st.Page("overview_page.py", title="Overview", url_path="overview", default=True),
    st.Page("pages/1_Live_map.py", title="Live map", url_path="live-map"),
    st.Page("pages/2_Line_explorer.py", title="Line explorer", url_path="line-explorer"),
    st.Page("pages/3_Pipeline_health.py", title="Pipeline health", url_path="pipeline-health"),
    st.Page("pages/4_About.py", title="About", url_path="about"),
]
st.navigation(pages).run()
