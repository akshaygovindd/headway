"""Build a synthetic curated window from real days, for memory and duration measurements.

Takes a copy of the real curated tree (``aws s3 sync`` of ``curated/``), keeps the static GTFS
tables, and writes ``--days`` days ending ``--end`` by date-shifting the complete real days
(``--sources``): every timestamp and date column moves by whole days, ``capture_id`` gets a suffix
so shifted copies stay distinct. With ``--vehicle-polls 4`` each vehicle position row becomes four
rows 15 s apart (and each vehicle manifest four), the Release 2 sampling of ADR-014.

    uv run python scripts/synth_window.py --src A/curated --dst B/curated \
        --days 7 --end 2026-11-25 --sources 2026-11-23,2026-11-24 --vehicle-polls 4
"""

import argparse
import shutil
from datetime import date, timedelta
from pathlib import Path

import duckdb

CITY = "boston"
SHIFT_COLUMNS = {
    "vehicle_positions": ["fetched_at", "feed_header_ts", "event_ts"],
    "trip_updates": [
        "fetched_at", "feed_header_ts", "start_date", "predicted_arrival_ts",
        "predicted_departure_ts", "event_ts",
    ],
    "alerts": ["fetched_at", "feed_header_ts", "active_start", "active_end"],
    "manifests": ["scheduled_at", "fetched_at", "feed_header_ts"],
    "quarantine": ["fetched_at"],
}  # fmt: skip


def shifted(table: str, days: int, k: int, polls: int) -> str:
    """The SELECT list: the source columns with dates moved by `days` and poll offset `k`."""
    offset = (
        f"to_days({days}) + to_seconds({15 * k})"
        if table == "vehicle_positions"
        else (f"to_days({days})")
    )
    items = [f"{c} + {offset} AS {c}" for c in SHIFT_COLUMNS[table]]
    items.append(f"dt + to_days({days}) AS dt")
    if "capture_id" in _COLUMNS[table]:
        suffix = ("-d" + str(days) if days else "") + (f"-q{k}" if polls > 1 and k else "")
        if suffix:
            items.append(f"capture_id || '{suffix}' AS capture_id")
    return "* REPLACE (" + ", ".join(items) + ")"


_COLUMNS: dict[str, set[str]] = {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--dst", type=Path, required=True)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--sources", required=True, help="comma separated complete real days")
    ap.add_argument("--vehicle-polls", type=int, default=4)
    args = ap.parse_args()
    sources = [date.fromisoformat(s) for s in args.sources.split(",")]
    if args.dst.exists():
        shutil.rmtree(args.dst)
    city_src, city_dst = args.src / CITY, args.dst / CITY
    shutil.copytree(city_src / "static", city_dst / "static")
    con = duckdb.connect()
    con.execute("set preserve_insertion_order=false")
    rows: dict[str, int] = {}
    targets = [args.end - timedelta(days=i) for i in range(args.days - 1, -1, -1)]
    for n, target in enumerate(targets):
        source = sources[n % len(sources)]
        days = (target - source).days
        for table in SHIFT_COLUMNS:
            for file in sorted(
                (city_src / f"table={table}" / f"dt={source}").glob("hour=*/*.parquet")
            ):
                _COLUMNS[table] = {r[0] for r in con.execute(
                    f"describe select * from read_parquet('{file}')").fetchall()}  # fmt: skip
                polls = args.vehicle_polls if table in ("vehicle_positions",) else 1
                out_dir = city_dst / f"table={table}" / f"dt={target}" / file.parent.name
                out_dir.mkdir(parents=True, exist_ok=True)
                # a vehicle manifest per extra poll: the capture the extra rows came from
                only_vehicle = " where feed = 'vehicle_positions'" if table == "manifests" else ""
                polls = args.vehicle_polls if table == "manifests" else polls
                selects = " union all ".join(
                    f"select {shifted(table, days, k, polls)} from read_parquet('{file}')"
                    + (only_vehicle if k else "")
                    for k in range(polls)
                )
                out = out_dir / file.name
                con.execute(f"copy ({selects}) to '{out}' (format parquet)")
                rows[table] = rows.get(table, 0) + int(
                    con.execute(f"select count(*) from read_parquet('{out}')").fetchone()[0]  # type: ignore[index]
                )
    for table, n in sorted(rows.items()):
        print(f"{table}: {n} rows over {len(targets)} days")  # noqa: T201


if __name__ == "__main__":
    main()
