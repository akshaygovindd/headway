"""Expanding-window weekly folds: the test week is always after every training week."""

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

WEEK_DAYS = 7


@dataclass(frozen=True)
class Fold:
    index: int
    train_start: date
    train_end: date  # inclusive
    test_start: date
    test_end: date  # inclusive


def make_folds(service_dates: pd.Series, min_train_weeks: int = 1) -> list[Fold]:
    """Weeks are 7-day blocks counted from the first service date. Fold k trains on weeks 0..k-1
    and tests on week k, for k from min_train_weeks. Fewer weeks than min_train_weeks + 1 give no
    folds. The last test week may be partial."""
    if service_dates.empty:
        return []
    first = min(service_dates)
    last = max(service_dates)
    n_weeks = (last - first).days // WEEK_DAYS + 1
    folds = [
        Fold(
            index=k,
            train_start=first,
            train_end=first + timedelta(days=WEEK_DAYS * k - 1),
            test_start=first + timedelta(days=WEEK_DAYS * k),
            test_end=first + timedelta(days=WEEK_DAYS * (k + 1) - 1),
        )
        for k in range(max(min_train_weeks, 1), n_weeks)
    ]
    check_folds(folds)
    return folds


def check_folds(folds: list[Fold]) -> None:
    """Raise unless every test week starts after its training weeks end and test weeks are
    disjoint and in order."""
    previous_test_end: date | None = None
    for f in folds:
        if not f.train_start <= f.train_end < f.test_start <= f.test_end:
            raise ValueError(f"fold {f.index}: test week does not follow the training weeks")
        if previous_test_end is not None and f.test_start <= previous_test_end:
            raise ValueError(f"fold {f.index}: test week overlaps the previous one")
        previous_test_end = f.test_end


def split(df: pd.DataFrame, fold: Fold) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = df["service_date"]
    train = df[(d >= fold.train_start) & (d <= fold.train_end)]
    test = df[(d >= fold.test_start) & (d <= fold.test_end)]
    return train, test
