"""Streamlit helpers shared by the pages: page chrome, cached loading and metric cards.

No metric formulas live here or in the pages; they call `lib` for every number.
"""

from typing import Any

import pandas as pd
import streamlit as st

from lib import data
from lib.coverage import WITHHELD_TEXT, CoverageBadge, coverage_badge, is_withheld
from lib.freshness import format_age, gold_age, snapshot_age
from lib.timefmt import format_local

TITLE = "Headway"
SUBTITLE = "Near-Real-Time Transit Reliability & AI Analytics"
TAGLINE = "Know the gap. Measure the reliability."
DISCLAIMER = "Headway approximations, not official MBTA metrics."

_BADGE_COLORS = {"normal": "green", "low": "orange", "insufficient": "red"}


@st.cache_data(ttl=300, show_spinner=False)
def gold_table(base: str, name: str) -> pd.DataFrame:
    return data.load_table(base, name)


@st.cache_data(ttl=300, show_spinner=False)
def gold_table_optional(base: str, name: str) -> pd.DataFrame | None:
    """A Release 2 table; None (page hides the section) when the export has no such file."""
    return data.load_optional_table(base, name)


@st.cache_data(ttl=300, show_spinner=False)
def gold_manifest(base: str) -> dict[str, Any]:
    return data.load_manifest(base)


@st.cache_data(ttl=60, show_spinner=False)
def live_snapshot(base: str) -> dict[str, Any]:
    return data.load_snapshot(base)


def data_base() -> str:
    """The configured data root; stops the page with a clear message when unavailable."""
    try:
        return data.base_url()
    except data.DataUnavailableError as exc:
        st.error(f"Data source not configured: {exc}")
        st.stop()


def page_header(page_title: str) -> None:
    st.set_page_config(page_title=f"{page_title} · Headway", layout="wide")
    st.title(TITLE)
    st.subheader(SUBTITLE)
    st.caption(f"**{TAGLINE}** · {DISCLAIMER}")
    st.header(page_title)


def freshness_text(manifest: dict[str, Any], snapshot: dict[str, Any] | None) -> str:
    """Human-readable feed freshness: when gold was exported and when the live feed was read."""
    parts = [f"gold tables exported {format_age(gold_age(manifest))}"]
    if snapshot is not None:
        parts.append(f"live snapshot generated {format_age(snapshot_age(snapshot))}")
    return " · ".join(parts)


def show_as_of_and_freshness(
    manifest: dict[str, Any], snapshot: dict[str, Any] | None = None
) -> None:
    """Two separate lines: the data period the metrics describe, and how fresh the feeds are."""
    metrics_as_of = manifest.get("metrics_as_of")
    st.markdown(f"**Metrics as of:** {format_local(metrics_as_of) if metrics_as_of else 'unknown'}")
    st.markdown(f"**Feed freshness:** {freshness_text(manifest, snapshot)}")


def badge_markdown(badge: CoverageBadge) -> str:
    return f":{_BADGE_COLORS[badge.level]}[{badge.label}]"


def _fmt_seconds(value: float | None, *, signed: bool = True) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:+.0f} s" if signed else f"{abs(value):.0f} s"


def has_values(*series: Any) -> bool:
    """True when at least one finite number is present (guards charts against empty/all-NaN)."""
    return any(pd.to_numeric(pd.Series(s), errors="coerce").notna().any() for s in series)


def render_metric_card(
    label: str,
    values: dict[str, float | None],
    *,
    coverage_pct: float | None,
    freshness: str | None,
    disruption: str | None = None,
) -> None:
    """Render one metric card. Coverage and freshness are required: no number without them.

    `values` maps a caption to a value in seconds (or a share between 0 and 1 for captions
    containing "share"). Captions containing "revision" are absolute and shown unsigned.
    Below 50% coverage the numbers are withheld. `freshness` must be passed explicitly; pass
    None when the page already shows it once at the top.
    """
    badge = coverage_badge(coverage_pct)
    with st.container(border=True):
        head, tag = st.columns([2, 3])
        head.subheader(label)
        tag.markdown(badge_markdown(badge))
        if disruption:
            st.markdown(f":orange[{disruption}]")
        if not badge.rankable:
            st.write("Not enough data to report values.")
        else:
            for caption, value in values.items():
                shown = (
                    "n/a"
                    if value is None or pd.isna(value)
                    else f"{value:.1%}"
                    if "share" in caption
                    else _fmt_seconds(value, signed="revision" not in caption)
                )
                st.metric(caption, shown)
        cov_text = (
            "n/a" if coverage_pct is None or pd.isna(coverage_pct) else f"{coverage_pct:.0f}%"
        )
        st.caption(f"Coverage {cov_text}" + (f" · {freshness}" if freshness else ""))


def fmt_seconds(value: float | None, *, signed: bool = True) -> str:
    return _fmt_seconds(value, signed=signed)


def fmt_pct(value: float | None) -> str:
    """Whole percent, or n/a."""
    return "n/a" if value is None or pd.isna(value) else f"{value:.0f}%"


def cell_seconds(value: float | None, coverage_pct: float | None, *, signed: bool = True) -> str:
    """Table cell in seconds; the withholding text below 50% coverage."""
    return WITHHELD_TEXT if is_withheld(coverage_pct) else _fmt_seconds(value, signed=signed)


def cell_pct(value: float | None, coverage_pct: float | None) -> str:
    """Table cell in whole percent; the withholding text below 50% coverage."""
    return WITHHELD_TEXT if is_withheld(coverage_pct) else fmt_pct(value)


def fmt_ratio(value: float | None) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value:.2f}×"


def coverage_text(coverage_pct: float | None, n: float | None = None) -> str:
    """Coverage (and the count behind a value) shown beside every Release 2 number."""
    cov = fmt_pct(coverage_pct)
    return f"coverage {cov}" + ("" if n is None or pd.isna(n) else f", n={int(n):,}")
