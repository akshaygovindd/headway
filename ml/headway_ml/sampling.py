"""Stratified cap on the TRAINING rows of each service day.

The examples of one day are strongly correlated (every trip stop of every slot), and the job's
memory and time grow with the rows, so training folds may be capped per day. The cap is stratified
by line x horizon so that small lines (Mattapan) keep their rows: strata smaller than their fair
share keep every row and the budget they leave over goes to the larger ones (water filling).
Selection is a seeded random draw per day, reproducible for the same rows in the same order.

Only training frames are sampled. Scoring always uses the full, unsampled test week.
"""

import numpy as np
import pandas as pd

STRATA = ["line", "horizon_min"]
STATS_COLUMNS = [*STRATA, "n_before", "n_after"]


def allocate(counts: dict[tuple[str, int], int], budget: int) -> dict[tuple[str, int], int]:
    """Rows kept per stratum: all of a small stratum, an equal share of what is left for the
    others. Every non-empty stratum keeps at least one row."""
    out: dict[tuple[str, int], int] = {}
    remaining = budget
    items = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]))
    for i, (key, count) in enumerate(items):
        share = max(remaining // (len(items) - i), 1)
        out[key] = min(count, share)
        remaining = max(remaining - out[key], 0)
    return out


def stratified_cap(
    df: pd.DataFrame, cap_per_day: int | None, seed: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(sample, stats). stats has one row per line x horizon with the rows before and after,
    summed over the days. cap_per_day None keeps everything."""
    if df.empty or cap_per_day is None:
        keep = df
    else:
        parts = []
        for day, g in df.groupby("service_date", sort=True):
            if len(g) <= cap_per_day:
                parts.append(g)
                continue
            rng = np.random.default_rng([seed, pd.Timestamp(str(day)).toordinal()])
            order = pd.Series(rng.random(len(g)), index=g.index)
            rank = order.groupby([g["line"], g["horizon_min"]]).rank(method="first")
            sizes = g.groupby(STRATA).size().reset_index(name="n")
            counts = {
                (str(a), int(b)): int(n)
                for a, b, n in zip(sizes["line"], sizes["horizon_min"], sizes["n"], strict=True)
            }
            quota = allocate(counts, cap_per_day)
            limit = pd.Series(
                [quota[(str(a), int(b))] for a, b in zip(g["line"], g["horizon_min"], strict=True)],
                index=g.index,
            )
            parts.append(g[rank <= limit])
        keep = pd.concat(parts) if parts else df
    before = df.groupby(STRATA).size().rename("n_before")
    after = keep.groupby(STRATA).size().rename("n_after")
    stats = pd.concat([before, after], axis=1).fillna(0).astype(int).reset_index()
    return keep.sort_index(), stats[STATS_COLUMNS]


def rows_by_line(stats: pd.DataFrame) -> dict[str, int]:
    return {str(k): int(v) for k, v in stats.groupby("line")["n_after"].sum().items()}
