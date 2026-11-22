"""Frames for the Line explorer page."""

import pandas as pd

from lib.coverage import NORMAL_MIN
from lib.timefmt import WEEKDAYS, date_label, hour_label, hour_start_utc, local_date, weekday_name

SERVICE_HOUR_ORDER = [*range(3, 24), 0, 1, 2]


def _select(df: pd.DataFrame, line: str, direction: int | None) -> pd.DataFrame:
    out = df[df["line"] == line]
    return out if direction is None else out[out["direction_id"] == direction]


def directions(df: pd.DataFrame, line: str) -> list[int]:
    return sorted(int(d) for d in df.loc[df["line"] == line, "direction_id"].dropna().unique())


def _wmean(values: pd.Series, weights: pd.Series) -> float | None:
    mask = values.notna() & (weights > 0)
    if not mask.any():
        return None
    return float((values[mask] * weights[mask]).sum() / weights[mask].sum())


def heatmap(hourly: pd.DataFrame, line: str, direction: int | None) -> pd.DataFrame:
    """Hour x weekday cells with median deviation and coverage (long format).

    Columns: weekday, hour_local, hour, median_deviation_s, coverage_pct.
    """
    sel = _select(hourly, line, direction)
    rows: list[dict[str, object]] = []
    keyed = sel.assign(
        weekday=[
            weekday_name(d, int(h))
            for d, h in zip(sel["service_date"], sel["hour_local"], strict=True)
        ]
    )
    for (weekday, hour), g in keyed.groupby(["weekday", "hour_local"]):
        denom = float(g["observable_trip_stops"].sum())
        rows.append(
            {
                "weekday": weekday,
                "hour_local": int(hour),
                "hour": hour_label(int(hour)),
                "median_deviation_s": _wmean(
                    g["median_predicted_deviation_s"], g["n_eligible"].astype(float)
                ),
                "coverage_pct": 100.0 * float(g["coverage_numerator"].sum()) / denom
                if denom
                else None,
            }
        )
    return pd.DataFrame(
        rows, columns=["weekday", "hour_local", "hour", "median_deviation_s", "coverage_pct"]
    )


def heatmap_matrix(cells: pd.DataFrame, column: str = "median_deviation_s") -> pd.DataFrame:
    """Pivot heatmap cells to rows = hours (service-day order), columns = Mon..Sun."""
    matrix = cells.pivot(index="hour_local", columns="weekday", values=column)
    hours = [h for h in SERVICE_HOUR_ORDER if h in matrix.index]
    days = [d for d in WEEKDAYS if d in matrix.columns]
    matrix = matrix.reindex(index=hours, columns=days)
    matrix.index = pd.Index([hour_label(int(h)) for h in matrix.index], name="hour")
    return matrix


def deviation_by_hour(hourly: pd.DataFrame, line: str, direction: int | None) -> pd.DataFrame:
    """Median and p90 deviation per local hour; `label` is a plain string for chart axes."""
    sel = _select(hourly, line, direction)
    rows: list[dict[str, object]] = []
    for (sd, hour), g in sel.groupby(["service_date", "hour_local"]):
        w = g["n_eligible"].astype(float)
        rows.append(
            {
                "start_utc": hour_start_utc(sd, int(hour)),
                "label": f"{date_label(local_date(sd, int(hour)))} {hour_label(int(hour))}",
                "median_deviation_s": _wmean(g["median_predicted_deviation_s"], w),
                "p90_deviation_s": _wmean(g["p90_predicted_deviation_s"], w),
                "coverage_pct": 100.0
                * float(g["coverage_numerator"].sum())
                / float(g["observable_trip_stops"].sum()),
            }
        )
    frame = pd.DataFrame(
        rows,
        columns=["start_utc", "label", "median_deviation_s", "p90_deviation_s", "coverage_pct"],
    )
    return frame.sort_values("start_utc").reset_index(drop=True)


def slowest_stretches(
    stops: pd.DataFrame,
    dim_stop: pd.DataFrame,
    line: str,
    direction: int,
    top: int = 10,
    min_coverage: float = NORMAL_MIN,
) -> pd.DataFrame:
    """Consecutive stop pairs with the largest rise in median deviation.

    Stops are ordered by stop_sequence. A pair counts only when both stops have at least
    `min_coverage` percent coverage. rise_s = deviation at the second stop minus the first.
    """
    sel = _select(stops, line, direction)
    cols = [
        "from_stop",
        "to_stop",
        "rise_s",
        "from_deviation_s",
        "to_deviation_s",
        "min_coverage_pct",
    ]
    per_stop: list[dict[str, object]] = []
    for stop_id, g in sel.groupby("stop_id"):
        denom = float(g["coverage_denominator"].sum())
        per_stop.append(
            {
                "stop_id": str(stop_id),
                "stop_sequence": float(g["stop_sequence"].median()),
                "deviation": _wmean(
                    g["median_predicted_deviation_s"], g["n_eligible"].astype(float)
                ),
                "coverage": 100.0 * float(g["coverage_numerator"].sum()) / denom if denom else 0.0,
            }
        )
    if len(per_stop) < 2:
        return pd.DataFrame(columns=cols)
    ordered = (
        pd.DataFrame(per_stop).sort_values(["stop_sequence", "stop_id"]).reset_index(drop=True)
    )
    names = dict(zip(dim_stop["stop_id"], dim_stop["stop_name"], strict=True))
    pairs: list[dict[str, object]] = []
    for a, b in zip(ordered.to_dict("records"), ordered.iloc[1:].to_dict("records"), strict=False):
        if a["coverage"] < min_coverage or b["coverage"] < min_coverage:
            continue
        if (
            a["deviation"] is None
            or b["deviation"] is None
            or pd.isna(a["deviation"])
            or pd.isna(b["deviation"])
        ):
            continue
        pairs.append(
            {
                "from_stop": names.get(a["stop_id"], a["stop_id"]),
                "to_stop": names.get(b["stop_id"], b["stop_id"]),
                "rise_s": b["deviation"] - a["deviation"],
                "from_deviation_s": a["deviation"],
                "to_deviation_s": b["deviation"],
                "min_coverage_pct": min(a["coverage"], b["coverage"]),
            }
        )
    out = pd.DataFrame(pairs, columns=cols)
    return out.sort_values("rise_s", ascending=False).head(top).reset_index(drop=True)
