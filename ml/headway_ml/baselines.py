"""Baselines and the MBTA reference, scored on exactly the examples the model will be.

persistence          last_upstream_delay_s (the same trip's last known delay)
daytype_hour         mean delay of the line by day type and hour over the TRAINING weeks of a fold
rolling_median_60m   rolling_median_delay_60m (a feature: the line's 60-minute median)
mbta_reference       MBTA's predicted deviation: a labelled reference line, not a baseline

Missing predictions are reported two ways. Strict view: only rows where every active method has a
value. Full view: every labelled row, a missing value replaced by the next one in the method's
fallback chain (below); the share replaced is reported as fallback_share, and a row whose whole
chain is empty is left out and counted (n_unscored).
"""

import pandas as pd

PERSISTENCE = "persistence"
DAYTYPE_HOUR = "daytype_hour"
ROLLING = "rolling_median_60m"
MBTA = "mbta_reference"
METHODS = [PERSISTENCE, DAYTYPE_HOUR, ROLLING, MBTA]
REFERENCES = {MBTA}

RAW = {
    PERSISTENCE: "last_upstream_delay_s",
    ROLLING: "rolling_median_delay_60m",
    MBTA: "mbta_predicted_deviation_s",
}
# persistence -> rolling median is the documented chain; the others end in the same two
FALLBACK_CHAIN = {
    PERSISTENCE: [PERSISTENCE, ROLLING, DAYTYPE_HOUR],
    ROLLING: [ROLLING, PERSISTENCE, DAYTYPE_HOUR],
    DAYTYPE_HOUR: [DAYTYPE_HOUR, ROLLING, PERSISTENCE],
    MBTA: [MBTA, PERSISTENCE, ROLLING, DAYTYPE_HOUR],
}
CELL = ["line", "day_type", "hour_local"]


def fit_daytype_hour(train: pd.DataFrame) -> pd.DataFrame:
    """Mean label per line, day type and hour. Takes the training rows and nothing else."""
    return train.groupby(CELL)["label_delay_s"].mean().rename("daytype_hour_pred").reset_index()


def raw_predictions(test: pd.DataFrame, table: pd.DataFrame | None) -> pd.DataFrame:
    """One column per method, NaN where the method has no value for the row (same index as test)."""
    out = pd.DataFrame(index=test.index)
    for method, column in RAW.items():
        out[method] = test[column].astype("float64")
    if table is None:
        out[DAYTYPE_HOUR] = float("nan")
    else:
        merged = test[CELL].merge(table, on=CELL, how="left")
        out[DAYTYPE_HOUR] = merged["daytype_hour_pred"].to_numpy()
    return out[METHODS]


def filled_predictions(raw: pd.DataFrame, methods: list[str]) -> pd.DataFrame:
    """Raw predictions with each missing value taken from the method's fallback chain."""
    out = pd.DataFrame(index=raw.index)
    for m in methods:
        chain = [c for c in FALLBACK_CHAIN[m] if c in methods]
        col = raw[chain[0]].copy()
        for c in chain[1:]:
            col = col.fillna(raw[c])
        out[m] = col
    return out
