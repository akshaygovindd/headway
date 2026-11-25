"""Helpers for the withholding policy: no value is rendered where coverage is below 50%."""

import re

import pandas as pd
from streamlit.testing.v1 import AppTest

from lib.coverage import LOW_MIN, WITHHELD_TEXT

# Columns that describe the row or the count behind it; every other column is a value.
IDENTIFIER_COLUMNS = {
    "Line", "Minutes ahead", "Time band", "Coverage", "n", "Trips",
    "of which service disruption", "Horizon", "Method", "Hour",
    "Countdown", "Station", "Could not be checked",
}  # fmt: skip


def table_violations(frame: pd.DataFrame) -> list[str]:
    """Rows of a rendered table that show a value although their Coverage is below 50%."""
    if "Coverage" not in frame:
        return []
    bad = []
    for _, row in frame.iterrows():
        match = re.match(r"(\d+)%", str(row["Coverage"]))
        if match and float(match.group(1)) >= LOW_MIN:
            continue  # "n/a" coverage counts as below the bar
        for column in frame.columns:
            if column not in IDENTIFIER_COLUMNS and str(row[column]) != WITHHELD_TEXT:
                bad.append(f"{row.to_dict()} shows {column}={row[column]}")
    return bad


def rendered_text(at: AppTest) -> str:
    """Every text a page renders: markdown, captions, metrics and table cells."""
    parts = [str(e.value) for e in (*at.markdown, *at.caption, *at.subheader, *at.info, *at.metric)]
    parts += [df.value.to_string() for df in at.dataframe]
    return "\n".join(parts)


def assert_withheld(at: AppTest) -> None:
    assert not at.exception, [e.value for e in at.exception]
    problems = [p for df in at.dataframe for p in table_violations(df.value)]
    assert not problems, problems
