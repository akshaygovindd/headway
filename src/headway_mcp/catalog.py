"""What the tools accept: lines, windows and the metrics of docs/METRICS.md with their tables."""

from dataclasses import dataclass
from typing import Any

LINES = ("Red", "Orange", "Blue", "Green-B", "Green-C", "Green-D", "Green-E", "Mattapan")
GRANULARITIES = ("hourly", "daily")
METRICS_DOC = "docs/METRICS.md"


@dataclass(frozen=True)
class Source:
    """One gold table that holds a metric, with the column to read and how its rows are keyed."""

    table: str
    column: str
    scale: str = ""  # SQL factor applied to the column, e.g. "100.0 *"
    n_column: str = "n_eligible"
    extras: tuple[str, ...] = ()  # extra key columns (time_band, day_type)
    history_family: str | None = None  # metric family of history/ (daily and hourly tables)


@dataclass(frozen=True)
class Metric:
    name: str  # ends in the unit (_s, _pct): the check reads the unit from it
    label: str
    anchor: str  # heading anchor in docs/METRICS.md
    higher_is_worse: bool
    window: Source | None = None
    daily: Source | None = None
    hourly: Source | None = None
    windows: tuple[str, ...] = ("last_24h",)


# Version of each history family; keep in step with headway_transform.history.METRIC_VERSIONS.
HISTORY_VERSIONS = {"actual_deviation": 2, "headway_adherence": 2, "ett": 2}

_REL_W = "agg_line_reliability_window"
_REL_H = "agg_line_reliability_hourly"


def _predicted(col: str, scale: str = "") -> dict[str, Any]:
    return {
        "window": Source(_REL_W, col, scale),
        "hourly": Source(_REL_H, col, scale),
    }


def _actual(col: str) -> dict[str, Any]:
    return {
        "window": Source("agg_actual_deviation_window", col, n_column="n_arrivals"),
        "daily": Source("agg_actual_deviation_daily", col, n_column="n_arrivals",
                        history_family="actual_deviation"),
        "hourly": Source("agg_actual_deviation_hourly", col, n_column="n_arrivals",
                         history_family="actual_deviation"),
    }  # fmt: skip


def _headway(col: str) -> dict[str, Any]:
    return {
        "window": Source("agg_headway_window", col, n_column="n_headways"),
        "daily": Source("agg_headway_adherence_daily", col, n_column="n_headways",
                        history_family="headway_adherence"),
        "hourly": Source("agg_headway_adherence_hourly", col, n_column="n_headways"),
    }  # fmt: skip


def _ett(col: str) -> dict[str, Any]:
    return {
        "window": Source("agg_ett_window", col, n_column="n_trips"),
        "daily": Source("agg_ett_daily", col, n_column="n_trips", extras=("time_band", "day_type"),
                        history_family="ett"),
    }  # fmt: skip


_W7 = ("last_24h", "last_7d")

METRICS: dict[str, Metric] = {
    m.name: m
    for m in (
        Metric(
            "predicted_deviation_median_s",
            "Median predicted schedule deviation (s)",
            "1-predicted-schedule-deviation",
            True,
            **_predicted("median_predicted_deviation_s"),
        ),
        Metric(
            "predicted_deviation_p90_s",
            "90th percentile predicted schedule deviation (s)",
            "1-predicted-schedule-deviation",
            True,
            **_predicted("p90_predicted_deviation_s"),
        ),
        Metric(
            "prediction_revision_median_s",
            "Median absolute prediction revision (s)",
            "2-prediction-revision",
            True,
            **_predicted("median_abs_revision_s"),
        ),
        Metric(
            "coverage_pct",
            "Arrival coverage (%)",
            "3-coverage",
            False,
            **_predicted("coverage_pct"),
        ),
        Metric(
            "skipped_share_pct",
            "Share of skipped stops (%)",
            "1-predicted-schedule-deviation",
            True,
            **_predicted("skipped_share", "100.0 *"),
        ),
        Metric(
            "actual_deviation_median_s",
            "Median actual schedule deviation (s)",
            "7-actual-schedule-deviation-release-2",
            True,
            windows=_W7,
            **_actual("median_deviation_s"),
        ),
        Metric(
            "actual_deviation_p90_s",
            "90th percentile actual schedule deviation (s)",
            "7-actual-schedule-deviation-release-2",
            True,
            windows=_W7,
            **_actual("p90_deviation_s"),
        ),
        Metric(
            "on_time_pct",
            "Arrivals within -60 to +300 s of schedule (%)",
            "7-actual-schedule-deviation-release-2",
            False,
            windows=_W7,
            **_actual("pct_on_time_window"),
        ),
        Metric(
            "headway_within_band_pct",
            "Headways within the band (%)",
            "8-headway-adherence-release-2",
            False,
            windows=_W7,
            **_headway("pct_within_band"),
        ),
        Metric(
            "headway_bunched_pct",
            "Bunched headways (%)",
            "8-headway-adherence-release-2",
            True,
            windows=_W7,
            **_headway("pct_bunched"),
        ),
        Metric(
            "headway_gap_pct",
            "Gap headways (%)",
            "8-headway-adherence-release-2",
            True,
            windows=_W7,
            **_headway("pct_gap"),
        ),
        Metric(
            "ett_median_s",
            "Median excess trip time (s)",
            "9-ett-approximation-release-2",
            True,
            windows=_W7,
            **_ett("median_ett_s"),
        ),
    )
}
