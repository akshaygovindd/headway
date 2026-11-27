"""Fit the dwell table that corrects bracketed arrivals (dbt seed reconstruction_dwell).

A bracketed stop is one where no capture saw the vehicle STOPPED_AT: the polls before and after
straddled the whole dwell. Interpolating by distance between those two observations puts the stop
a fraction f of the dwell too late (f is how far along the stretch the stop lies), so the model
subtracts f x (expected dwell of a *missed* visit). This script estimates that expected dwell from
our own observed STOPPED_AT data only; no MBTA or LAMP data is read.

Why not simply the median observed dwell: at one poll a minute a visit is seen with probability
about min(1, dwell / spacing), so observed stops over-represent long dwells, and a single sighting
carries no dwell information at all (127k of 133k visits had one). The fit therefore uses the split
of visits by number of sightings k (0 = bracketed, 1, 2 or more), which does identify the dwell
distribution:

  1. dwell D ~ Gamma(shape, mean) per line; a visit with dwell D is seen k times where k is
     floor(D / T) or one more, T being the spacing of the run's observations;
  2. per line, choose shape and mean so the predicted shares of k = 0 and k >= 2 match the data
     (the runs' spacings, 15 / 30 / 60 / 120 s classes, enter as a mixture);
  3. per line and stop, and per line, stop and hour bucket, keep the shape and fit only the mean
     from the share of k = 0, when there are enough visits;
  4. for each cell and each 1-minute spacing class T (about 60 s, and 90 s or more) store
     E[D | missed] = E[D (1 - D/T)+] / E[(1 - D/T)+], the expected dwell of a visit that no poll
     at spacing T would see.

The dwell distribution does not depend on the polling rate, but the correction has only been
validated at 1-minute sampling (it did not help on four hours of 15-second data), so only the
60 s and 120 s columns are written and runs observed every 15 or 30 s are left uncorrected until
the table is refitted on at least three full days of 15-second data and validated (ADR-026).

Fit on a window of service dates (default 2026-11-22 to 2026-11-24, all 1-minute sampling) from a
local build, then commit the CSV; refitting is a deliberate change of definition (bump the fact
version), never a side effect of the lookback window.

    uv run python scripts/fit_reconstruction_dwell.py --db .local/prod_test.duckdb
"""

import argparse
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "dbt" / "headway" / "seeds" / "reconstruction_dwell.csv"
CLASSES = (15, 30, 60, 120)
# Only the 1-minute classes are written: the data the table is fitted on and validated against is
# 1-minute sampling, so the model applies no correction to runs observed every 15 or 30 s until a
# refit on full days of 15-second data (ADR-026).
EMITTED_CLASSES = (60, 120)
SHAPES = (1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 20.0, 40.0)
# The shape is barely identified (0.1 to 7 % of visits have two sightings), so it is
# bounded from above: a shape of 12 is a coefficient of variation of 0.29. A prior, not a fit.
MAX_SHAPE = 12.0
MEANS = np.arange(5.0, 150.5, 0.5)
MIN_STOP_VISITS = 100
MIN_STOP_HOUR_VISITS = 50
DRAWS = 20000

# Keep in step with the spacing class and hour bucket expressions of fct_reconstructed_arrivals.
SPACING_CLASS_SQL = (
    "case when spacing_s <= 22.5 then 15 when spacing_s <= 45 then 30 "
    "when spacing_s <= 90 then 60 else 120 end"
)
HOUR_BUCKET_SQL = (
    "case when h < 5 then 'night' when h < 9 then 'am' when h < 15 then 'mid' "
    "when h < 19 then 'pm' else 'eve' end"
)


def quantiles(shape: float) -> np.ndarray:
    """A fixed sample of the unit-mean gamma(shape), sorted: the same on every run."""
    return np.sort(np.random.default_rng(1).gamma(shape, 1.0 / shape, DRAWS))


def p_miss(q: np.ndarray, mean: float, spacing: float) -> float:
    return float(np.mean(np.clip(1.0 - q * mean / spacing, 0.0, None)))


def p_two_plus(q: np.ndarray, mean: float, spacing: float) -> float:
    x = q * mean / spacing
    whole = np.floor(x)
    return float(np.mean(np.where(whole >= 2, 1.0, np.where(whole == 1, x - whole, 0.0))))


def missed_dwell(q: np.ndarray, mean: float, spacing: float) -> float:
    d = q * mean
    w = np.clip(1.0 - d / spacing, 0.0, None)
    return float((d * w).sum() / w.sum()) if w.sum() > 0 else 0.0


def fit_line(
    p0: float, p2: float, shares: dict[int, float], max_shape: float = MAX_SHAPE
) -> tuple[float, float]:
    """(shape, mean) matching the observed shares of k = 0 and k >= 2 for a mixture of spacings."""
    best: tuple[float, float, float] | None = None
    for shape in (x for x in SHAPES if x <= max_shape):
        q = quantiles(shape)
        for mean in MEANS[::2]:
            e0 = sum(s * p_miss(q, mean, t) for t, s in shares.items()) - p0
            e2 = sum(s * p_two_plus(q, mean, t) for t, s in shares.items()) - p2
            err = e0 * e0 + e2 * e2
            if best is None or err < best[0]:
                best = (err, shape, float(mean))
    assert best is not None
    return best[1], best[2]


def fit_mean(shape: float, p0: float, shares: dict[int, float]) -> float | None:
    """Mean dwell, shape fixed, matching the observed share of k = 0; None at the edge of the grid
    (the share cannot be matched, so the cell is left to the coarser level)."""
    q = quantiles(shape)
    preds = np.array(
        [sum(s * p_miss(q, float(m), t) for t, s in shares.items()) for m in MEANS[::2]]
    )
    i = int(np.argmin(np.abs(preds - p0)))
    return None if i in (0, len(preds) - 1) else float(MEANS[::2][i])


def visits(con: duckdb.DuckDBPyConnection, first: str, last: str) -> list[tuple[Any, ...]]:
    """One row per observed or bracketed visit: line, stop, hour bucket, k, spacing class."""
    return con.execute(
        f"""
        with ev as (
            select trip_instance_key, segment, vehicle_id, event_ts, current_status,
                current_stop_sequence,
                epoch(event_ts) - epoch(lag(event_ts) over (
                    partition by trip_instance_key, segment, vehicle_id
                    order by event_ts, capture_id)) as gap_s
            from int_vehicle_stop_events where exclusion_reason is null
        ),
        spacing as (
            select trip_instance_key, segment, vehicle_id, median(gap_s) as spacing_s
            from ev where gap_s is not null group by all
        ),
        sightings as (
            select trip_instance_key, segment, vehicle_id, current_stop_sequence as stop_sequence,
                count(*) as k
            from ev where current_status = 'STOPPED_AT' group by all
        )
        select f.line, f.stop_id,
            (select {HOUR_BUCKET_SQL} from (select extract(hour from
                timezone('America/New_York', f.scheduled_arrival_ts)) as h)) as bucket,
            coalesce(s.k, 0) as k,
            (select {SPACING_CLASS_SQL} from (select coalesce(sp.spacing_s, 60.0) as spacing_s))
                as spacing_class
        from fct_reconstructed_arrivals f
        left join sightings s on s.trip_instance_key = f.trip_instance_key
            and s.segment = f.segment and s.vehicle_id = f.vehicle_id
            and s.stop_sequence = f.stop_sequence
        left join spacing sp on sp.trip_instance_key = f.trip_instance_key
            and sp.segment = f.segment and sp.vehicle_id = f.vehicle_id
        where f.method in ('observed', 'bracketed') and f.scheduled_arrival_ts is not null
            and f.service_date between date '{first}' and date '{last}'
        """
    ).fetchall()


def class_shares(rows: list[tuple[Any, ...]]) -> dict[int, float]:
    counts = {c: 0 for c in CLASSES}
    for r in rows:
        counts[r[4]] += 1
    n = sum(counts.values())
    return {c: v / n for c, v in counts.items() if v}


def build(rows: list[tuple[Any, ...]], max_shape: float = MAX_SHAPE) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def emit(
        level: str, line: str, stop: str, bucket: str, n: int, shape: float, mean: float
    ) -> None:
        q = quantiles(shape)
        out.append(
            {
                "level": level,
                "line": line,
                "stop_id": stop,
                "hour_bucket": bucket,
                "n_visits": n,
                "shape": shape,
                "mean_dwell_s": round(mean, 1),
                **{f"missed_t{t}_s": round(missed_dwell(q, mean, t), 1) for t in EMITTED_CLASSES},
            }
        )

    lines = sorted({r[0] for r in rows})
    for line in lines:
        mine = [r for r in rows if r[0] == line]
        p0 = sum(1 for r in mine if r[3] == 0) / len(mine)
        p2 = sum(1 for r in mine if r[3] >= 2) / len(mine)
        shape, mean = fit_line(p0, p2, class_shares(mine), max_shape)
        emit("line", line, "", "", len(mine), shape, mean)
        by_stop: dict[str, list[tuple[Any, ...]]] = {}
        for r in mine:
            by_stop.setdefault(r[1], []).append(r)
        for stop, srows in sorted(by_stop.items()):
            if len(srows) >= MIN_STOP_VISITS:
                m = fit_mean(
                    shape, sum(1 for r in srows if r[3] == 0) / len(srows), class_shares(srows)
                )
                if m is not None:
                    emit("stop", line, stop, "", len(srows), shape, m)
            by_hour: dict[str, list[tuple[Any, ...]]] = {}
            for r in srows:
                by_hour.setdefault(r[2], []).append(r)
            for bucket, hrows in sorted(by_hour.items()):
                if len(hrows) >= MIN_STOP_HOUR_VISITS:
                    m = fit_mean(
                        shape, sum(1 for r in hrows if r[3] == 0) / len(hrows), class_shares(hrows)
                    )
                    if m is not None:
                        emit("stop_hour", line, stop, bucket, len(hrows), shape, m)
    return out


def write_csv(cells: list[dict[str, Any]], path: Path) -> None:
    cols = list(cells[0])
    lines = [",".join(cols)]
    for c in cells:
        lines.append(",".join(str(c[k]) for k in cols))
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / ".local" / "prod_test.duckdb")
    ap.add_argument("--from", dest="first", default="2026-11-22")
    ap.add_argument("--to", dest="last", default="2026-11-24")
    ap.add_argument("--out", type=Path, default=SEED)
    ap.add_argument("--max-shape", type=float, default=MAX_SHAPE, help="upper bound of the shape")
    args = ap.parse_args()
    con = duckdb.connect(str(args.db), read_only=True)
    rows = visits(con, args.first, args.last)
    if not rows:
        print("no visits in that range", file=sys.stderr)
        return 1
    cells = build(rows, args.max_shape)
    write_csv(cells, args.out)
    lines = [c for c in cells if c["level"] == "line"]
    print(f"{len(rows)} visits, {args.first} to {args.last}; {len(cells)} cells -> {args.out}")
    for c in lines:
        print(
            f"  {c['line']:9s} n={c['n_visits']:6d} shape={c['shape']:4.1f} "
            f"mean dwell {c['mean_dwell_s']:5.1f} s; missed at 60/120 s: "
            f"{c['missed_t60_s']}/{c['missed_t120_s']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
