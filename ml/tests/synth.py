"""Synthetic multi-week examples where the right answer is known."""

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

START = date(2026, 11, 2)  # a Monday


def seasonal(day_type: str, hour: int) -> float:
    return (120.0 if day_type == "weekend" else 30.0) + 15.0 * hour


def make_examples(
    weeks: int = 3,
    lines: tuple[str, ...] = ("Red", "Blue"),
    horizons: tuple[int, ...] = (15, 30, 60),
    *,
    persistence_exact: bool = False,
    seed: int = 1,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(weeks * 7):
        day = START + timedelta(days=d)
        day_type = "weekend" if day.weekday() >= 5 else "weekday"
        for hour in range(6, 22):
            for minute in (0, 15, 30, 45):
                t = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
                for line in lines:
                    for h in horizons:
                        base = seasonal(day_type, hour)
                        if persistence_exact:
                            label = float(rng.normal(0, 200))
                            last = label
                        else:
                            label = base
                            last = base + float(rng.normal(0, 300))
                        rows.append(
                            {
                                "prediction_ts": t,
                                "service_date": day,
                                "line": line,
                                "direction_id": 0,
                                "horizon_min": h,
                                "stop_position": 0.5,
                                "hour_local": hour,
                                "day_type": day_type,
                                "scheduled_headway_s": 300.0,
                                "last_upstream_delay_s": last,
                                "rolling_median_delay_30m": float(rng.normal(0, 300)),
                                "rolling_median_delay_60m": float(rng.normal(0, 300)),
                                "headway_within_share": 0.8,
                                "active_disruption_alert": bool(rng.random() < 0.1),
                                "skipped_share_1h": 0.0,
                                "mbta_predicted_deviation_s": label + float(rng.normal(0, 60)),
                                "label_delay_s": label,
                            }
                        )
    df = pd.DataFrame(rows)
    df["period_type"] = np.where(df["active_disruption_alert"], "alert_active", "normal")
    return df


def make_gate(
    lines: tuple[str, ...] = ("Red", "Blue"), passed: tuple[str, ...] = ()
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "line": list(lines),
            "n_days": [21 if x in passed else 2 for x in lines],
            "n_weekend_days": [6 if x in passed else 1 for x in lines],
            "min_days": 21,
            "min_weekend_days": 3,
            "gate_passed": [x in passed for x in lines],
        }
    )
