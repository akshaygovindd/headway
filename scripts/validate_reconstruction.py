"""Cross-check reconstructed arrivals against MBTA's LAMP subway on-time-performance files.

Source: https://performancedata.mbta.com/lamp/subway-on-time-performance-v1/ (daily Parquet, one row
per trip_id x stop_id x service_date). Use is governed by the MassDOT Developers License Agreement
(use, reproduce and redistribute, with MassDOT acknowledged as the provider); see
docs/VALIDATION.md. The files are downloaded to .local/validation/lamp (git-ignored) and never
committed. This is not part of `make check` or CI because it needs a download.

    make validate
    uv run python scripts/validate_reconstruction.py --download-only
    uv run python scripts/validate_reconstruction.py --facts .local/prod_test.duckdb --no-download

Matching (see docs/VALIDATION.md): pass 1 joins on service date, trip id, direction and stop id;
pass 2 pairs what is left by nearest scheduled arrival (same service date, line, direction, stop,
within 120 s), one to one. Differences are ours minus LAMP, in seconds.
"""

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://performancedata.mbta.com/lamp/subway-on-time-performance-v1"
DEFAULT_LAMP_DIR = ROOT / ".local" / "validation" / "lamp"
USER_AGENT = "headway-validation/1"
# Ingest switched from one vehicle poll a minute to four (every 15 s) at this instant.
SAMPLING_SWITCH_UTC = "2026-11-26 18:00:00+00"
PASS2_MAX_SCHEDULE_GAP_S = 120
# A candidate pair whose actual times are further apart than this is not the same train arriving
# (a reused trip id, or LAMP's ADDED trip inheriting a scheduled time); it is not accepted.
MAX_ARRIVAL_GAP_S = 600
# The dwell table of bracketed arrivals was fitted on service dates up to this one (our own
# STOPPED_AT data); later days are held out.
FIT_END = "2026-11-24"
CONFIDENCE_ORDER = ["high", "medium", "low", "missing"]


def lamp_file_name(service_date: str) -> str:
    return f"{service_date}-subway-on-time-performance-v1.parquet"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        data: bytes = resp.read()
    return data


def read_index(text: str) -> dict[str, dict[str, str]]:
    """Parse LAMP's index.csv into {service_date: row}."""
    return {row["service_date"]: row for row in csv.DictReader(io.StringIO(text))}


def download_days(days: list[str], lamp_dir: Path) -> dict[str, dict[str, object]]:
    """Fetch the index and each named service day that is listed; skip files already present.

    A day that is still being written (today's file) is refreshed when the index's size differs
    from the local file. Returns {day: {bytes, sha256, source}} for the days that exist.
    """
    lamp_dir.mkdir(parents=True, exist_ok=True)
    index = read_index(_get(f"{BASE_URL}/index.csv").decode())
    manifest_path = lamp_dir / "manifest.json"
    manifest: dict[str, dict[str, object]] = (
        json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    )
    for day in days:
        row = index.get(day)
        if row is None:
            print(f"  {day}: not in the LAMP index, skipped")
            continue
        path = lamp_dir / lamp_file_name(day)
        if path.exists() and path.stat().st_size == int(row["size_bytes"]):
            print(f"  {day}: cached ({path.stat().st_size} bytes)")
        else:
            print(f"  {day}: downloading {row['file_url']}")
            path.write_bytes(_get(row["file_url"]))
        manifest[day] = {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source": row["file_url"],
            "index_last_modified": row["last_modified"],
        }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def greedy_assign(pairs: list[tuple[int, int, float]]) -> list[tuple[int, int]]:
    """One-to-one assignment of (ours, theirs, cost) candidates, cheapest first."""
    used_a: set[int] = set()
    used_b: set[int] = set()
    out: list[tuple[int, int]] = []
    for a, b, _ in sorted(pairs, key=lambda p: (p[2], p[0], p[1])):
        if a not in used_a and b not in used_b:
            used_a.add(a)
            used_b.add(b)
            out.append((a, b))
    return out


def load_ours(con: duckdb.DuckDBPyConnection, facts_path: Path) -> None:
    """Create temp table ours from fct_reconstructed_arrivals in a read-only DuckDB file."""
    con.execute(f"attach '{facts_path}' as prod (read_only)")
    con.execute(
        f"""
        create temp table ours as
        select
            row_number() over (order by trip_instance_key, segment, stop_sequence) as row_id,
            service_date, line, trip_id, direction_id, stop_id, arrival_ts,
            scheduled_arrival_ts as scheduled_ts,
            method, coalesce(confidence, 'missing') as confidence, interval_width_s, bracket_gap_s,
            case when arrival_ts >= timestamptz '{SAMPLING_SWITCH_UTC}' then '15 s' else '1 min'
            end as period
        from prod.main.fct_reconstructed_arrivals
        """
    )


def load_mbta(con: duckdb.DuckDBPyConnection, files: list[Path]) -> None:
    """Create temp table mbta from LAMP day files; times are epoch seconds in the source."""
    listing = ", ".join(f"'{f}'" for f in files)
    con.execute(
        f"""
        create temp table mbta as
        select
            row_number() over () as mid,
            strptime(service_date::varchar, '%Y%m%d')::date as service_date,
            route_id as line, trip_id, direction_id::int as direction_id, stop_id,
            to_timestamp(stop_timestamp) as mbta_ts, dwell_time_seconds as dwell_s,
            -- scheduled times are seconds after the GTFS service-day origin (noon minus 12 h)
            ((strptime(service_date::varchar, '%Y%m%d') + interval 12 hour)
                at time zone 'America/New_York') - interval 12 hour
                + scheduled_arrival_time * interval 1 second as mbta_sched_ts
        from read_parquet([{listing}])
        """
    )


def drop_after_lamp_cutoff(con: duckdb.DuckDBPyConnection) -> int:
    """Leave out our rows after LAMP's last timestamp (the last day's file is partial when we
    read it, and our data runs later); returns how many were left out."""
    row = con.execute(
        """
        select count(*) from ours
        where coalesce(arrival_ts, scheduled_ts) > (select max(mbta_ts) from mbta)
        """
    ).fetchone()
    n = int(row[0]) if row else 0
    con.execute(
        "delete from ours "
        "where coalesce(arrival_ts, scheduled_ts) > (select max(mbta_ts) from mbta)"
    )
    return n


def match(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Fill temp table pairs(row_id, mid, pass) from tables ours and mbta; return counters."""
    keys = (
        "o.service_date = m.service_date and o.trip_id = m.trip_id "
        "and o.direction_id = m.direction_id and o.stop_id = m.stop_id and m.mbta_ts is not null"
    )
    plausible = (
        "(o.arrival_ts is null or "
        f"abs(epoch(o.arrival_ts) - epoch(m.mbta_ts)) <= {MAX_ARRIVAL_GAP_S})"
    )
    rows = con.execute(
        f"""
        select o.row_id, m.mid, coalesce(abs(epoch(o.arrival_ts) - epoch(m.mbta_ts)), 1e9)
        from ours o join mbta m on {keys}
        where {plausible}
        """
    ).fetchall()
    cand_count: dict[int, int] = {}
    for a, _, _ in rows:
        cand_count[a] = cand_count.get(a, 0) + 1
    first = greedy_assign(rows)
    con.execute("create temp table pairs (row_id bigint, mid bigint, pass int)")
    if first:
        con.executemany("insert into pairs values (?, ?, 1)", first)
    ambiguous = sum(1 for a, _ in first if cand_count[a] > 1)

    rest = con.execute(
        f"""
        select o.row_id, m.mid,
            abs(epoch(o.scheduled_ts) - epoch(m.mbta_sched_ts)) as gap
        from ours o
        join mbta m on o.service_date = m.service_date and o.line = m.line
            and o.direction_id = m.direction_id and o.stop_id = m.stop_id
        where m.mbta_ts is not null and o.scheduled_ts is not null
            and abs(epoch(o.scheduled_ts) - epoch(m.mbta_sched_ts)) <= {PASS2_MAX_SCHEDULE_GAP_S}
            and {plausible}
            and o.row_id not in (select row_id from pairs)
            and m.mid not in (select mid from pairs)
        """
    ).fetchall()
    second = greedy_assign(rest)
    if second:
        con.executemany("insert into pairs values (?, ?, 2)", second)
    return {"pass1": len(first), "pass1_ambiguous": ambiguous, "pass2": len(second)}


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


FIT_WINDOW = (
    f"case when o.service_date <= date '{FIT_END}' then 'fit (22-24 Nov)' "
    "else 'held out (25-26 Nov)' end"
)


def md_table(header: list[str], rows: list[tuple[Any, ...]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(_fmt(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def accuracy(
    con: duckdb.DuckDBPyConnection,
    by: str,
    *,
    diff: str = "epoch(o.arrival_ts) - epoch(m.mbta_ts)",
    where: str = "true",
    within: tuple[int, ...] = (30, 60),
    order: str | None = None,
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Match rate and difference statistics grouped by an expression of ours (o.*) or ALL."""
    shares = ", ".join(f"round(100 * avg((abs(d) <= {w})::int), 1)" for w in within)
    sql = f"""
        with j as (
            select {by} as grp, o.interval_width_s as w, o.bracket_gap_s as g, m.dwell_s as dw,
                p.mid is not null as matched, {diff} as d
            from ours o
            left join pairs p on p.row_id = o.row_id
            left join mbta m on m.mid = p.mid
            where o.arrival_ts is not null and ({where})
        )
        select grp, count(*) as n, count(*) filter (where matched) as n_matched,
            round(100.0 * count(*) filter (where matched) / count(*), 1) as match_pct,
            round(median(abs(d)), 1), round(quantile_cont(abs(d), 0.9), 1),
            round(median(d), 1), round(avg(d), 1), {shares}
        from j group by grp order by {order or "grp"}
    """
    header = ["group", "ours", "matched", "match %", "median abs s", "p90 abs s", "median bias s"]
    header += ["mean bias s"] + [f"within {w} s %" for w in within]
    return header, con.execute(sql).fetchall()


def coverage(con: duckdb.DuckDBPyConnection) -> dict[str, list[tuple[Any, ...]]]:
    """Why rows are unmatched, from both sides."""
    ours_side = con.execute(
        f"""
        select
            case
                when o.arrival_ts is null and p.mid is not null
                    then 'no arrival_ts (missing), LAMP has a time'
                when o.arrival_ts is null then 'no arrival_ts (missing), not in LAMP either'
                when p.mid is not null then 'matched'
                when not exists (select 1 from mbta m where m.service_date = o.service_date
                    and m.trip_id = o.trip_id) then 'trip not in LAMP'
                when not exists (select 1 from mbta m where m.service_date = o.service_date
                    and m.trip_id = o.trip_id and m.stop_id = o.stop_id) 
                    then 'stop not in LAMP trip'
                when not exists (select 1 from mbta m where m.service_date = o.service_date
                    and m.trip_id = o.trip_id and m.stop_id = o.stop_id and m.mbta_ts is not null)
                    then 'LAMP row has no stop_timestamp'
                when exists (select 1 from mbta m where m.service_date = o.service_date
                    and m.trip_id = o.trip_id and m.stop_id = o.stop_id and m.mbta_ts is not null
                    and abs(epoch(o.arrival_ts) - epoch(m.mbta_ts)) > {MAX_ARRIVAL_GAP_S})
                    then 'same trip and stop, more than 10 min apart (reused id?)'
                else 'other (direction or duplicate)'
            end as reason, count(*)
        from ours o left join pairs p on p.row_id = o.row_id
        group by 1 order by 2 desc
        """
    ).fetchall()
    mbta_side = con.execute(
        f"""
        select
            case
                when p.mid is not null and o.arrival_ts is null
                    then 'ours has no arrival_ts (missing)'
                when p.mid is not null then 'matched'
                when m.mbta_ts is null then 'no stop_timestamp'
                when m.trip_id not like '%-%' and not exists (select 1 from ours o
                    where o.service_date = m.service_date and o.trip_id = m.trip_id)
                    then 'scheduled trip not in our facts'
                when not exists (select 1 from ours o where o.service_date = m.service_date
                    and o.trip_id = m.trip_id) then 'unscheduled trip id (ADDED, NONREV, ...)'
                when not exists (select 1 from ours o where o.service_date = m.service_date
                    and o.trip_id = m.trip_id and o.stop_id = m.stop_id)
                    then 'trip in ours, stop not reconstructed'
                when exists (select 1 from ours o where o.service_date = m.service_date
                    and o.trip_id = m.trip_id and o.stop_id = m.stop_id
                    and abs(epoch(o.arrival_ts) - epoch(m.mbta_ts)) > {MAX_ARRIVAL_GAP_S})
                    then 'same trip and stop, more than 10 min apart (reused id?)'
                else 'other (our row is missing or duplicate)'
            end as reason, count(*)
        from mbta m
        left join pairs p on p.mid = m.mid
        left join ours o on o.row_id = p.row_id
        group by 1 order by 2 desc
        """
    ).fetchall()
    return {"ours": ours_side, "mbta": mbta_side}


def report(
    con: duckdb.DuckDBPyConnection,
    counters: dict[str, int],
    days: list[str],
    n_after_cutoff: int = 0,
) -> str:
    out: list[str] = [f"# LAMP validation results ({', '.join(days)})", ""]
    n_ours, n_ts = con.execute("select count(*), count(arrival_ts) from ours").fetchone() or (0, 0)
    n_mbta = con.execute("select count(*) from mbta").fetchone() or (0,)
    out += [
        f"Our rows {n_ours} ({n_ts} with arrival_ts); LAMP rows {n_mbta[0]}. Pass 1 (trip id) "
        f"{counters['pass1']} pairs ({counters['pass1_ambiguous']} had more than one candidate); "
        f"pass 2 (nearest scheduled arrival) {counters['pass2']} pairs. "
        f"{n_after_cutoff} of our rows fall after LAMP's last timestamp (its last day is partial) "
        "and are left out.",
        "",
    ]
    cov = coverage(con)
    out += ["## Our rows by outcome", "", md_table(["outcome", "rows"], cov["ours"]), ""]
    out += ["## LAMP rows by outcome", "", md_table(["outcome", "rows"], cov["mbta"]), ""]

    conf_order = (
        "case grp "
        + " ".join(f"when '{c}' then {i}" for i, c in enumerate(CONFIDENCE_ORDER))
        + " end"
    )
    sections: list[tuple[str, str, dict[str, Any]]] = [
        ("Overall", "'ALL'", {}),
        ("By line", "o.line", {}),
        ("By confidence", "o.confidence", {"order": conf_order}),
        ("By sampling period", "o.period", {}),
        ("By method", "o.method", {}),
        ("By method and confidence", "o.method || ' / ' || o.confidence", {}),
        ("By fit window and method", f"{FIT_WINDOW} || ' / ' || o.method", {}),
        ("By line and confidence", "o.line || ' / ' || o.confidence", {}),
        ("By confidence and sampling period", "o.confidence || ' / ' || o.period", {}),
        ("By line and sampling period", "o.line || ' / ' || o.period", {}),
        (
            "By observed interval width (s)",
            "case when o.interval_width_s <= 15 then '<=15' when o.interval_width_s <= 30 then "
            "'16-30' when o.interval_width_s <= 60 then '31-60' when o.interval_width_s <= 120 "
            "then '61-120' else '>120' end",
            {"where": "o.method = 'observed'", "order": "min(w)"},
        ),
    ]
    out += ["## Ours minus LAMP stop_timestamp", ""]
    for title, by, kw in sections:
        header, rows = accuracy(con, by, **kw)
        out += [f"### {title}", "", md_table(header, rows), ""]
    out += [
        "## Observed rows, shifted to the end of their interval",
        "",
        "LAMP's stop_timestamp is the first STOPPED_AT report; ours is the midpoint of the "
        "interval before it. Adding half the interval width puts ours on the same message.",
        "",
    ]
    end_diff = "epoch(o.arrival_ts) + o.interval_width_s / 2 - epoch(m.mbta_ts)"
    for title, by, kw in sections[:5]:
        kw2 = {**kw, "diff": end_diff, "within": (5, 30, 60)}
        kw2["where"] = "o.method = 'observed'" + (f" and ({kw['where']})" if "where" in kw else "")
        header, rows = accuracy(con, by, **kw2)
        out += [f"### {title}", "", md_table(header, rows), ""]
    gap = (
        "case when o.bracket_gap_s <= 30 then '<=30' when o.bracket_gap_s <= 60 then '31-60' "
        "when o.bracket_gap_s <= 120 then '61-120' when o.bracket_gap_s <= 180 then '121-180' "
        "when o.bracket_gap_s <= 300 then '181-300' else '>300' end"
    )
    out += ["## Bracketed rows (stop never seen STOPPED_AT)", ""]
    bracketed = "o.method = 'bracketed'"
    for title, by, kw3 in [
        ("By confidence", "o.confidence", {"order": conf_order}),
        ("By line", "o.line", {}),
        ("By fit window", FIT_WINDOW, {}),
        ("By fit window and sampling period", f"{FIT_WINDOW} || ' / ' || o.period", {}),
        ("By line and fit window", f"o.line || ' / ' || {FIT_WINDOW}", {}),
        ("By bracket gap (s)", gap, {"order": "min(g)"}),
    ]:
        header, rows = accuracy(con, by, where=bracketed, **kw3)
        out += [f"### {title}", "", md_table(header, rows), ""]
    out += [
        "## Bracketed rows by LAMP's dwell at the stop (diagnostic)",
        "",
        "LAMP's dwell is not an input of the model; this shows whether a dwell-dependent bias is "
        "left. Rows without a LAMP dwell are left out.",
        "",
    ]
    dwell = (
        "case when m.dwell_s <= 15 then '<=15' when m.dwell_s <= 30 then '16-30' "
        "when m.dwell_s <= 60 then '31-60' else '>60' end"
    )
    header, rows = accuracy(
        con, dwell, where=f"{bracketed} and m.dwell_s is not null", order="min(dw)"
    )
    out += [md_table(header, rows), ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--days", nargs="+", metavar="YYYY-MM-DD", help="default: dates in the facts")
    ap.add_argument("--facts", type=Path, default=ROOT / ".local" / "prod_test.duckdb")
    ap.add_argument("--lamp-dir", type=Path, default=DEFAULT_LAMP_DIR)
    ap.add_argument("--out", type=Path, default=ROOT / ".local" / "validation" / "results.md")
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--no-download", action="store_true", help="use files already in --lamp-dir")
    args = ap.parse_args()

    days = args.days
    if days is None:
        probe = duckdb.connect(str(args.facts), read_only=True)
        days = [
            str(r[0])
            for r in probe.execute(
                "select distinct service_date from fct_reconstructed_arrivals order by 1"
            ).fetchall()
        ]
        probe.close()
    if not args.no_download:
        download_days(days, args.lamp_dir)
    if args.download_only:
        return 0
    files = [
        args.lamp_dir / lamp_file_name(d)
        for d in days
        if (args.lamp_dir / lamp_file_name(d)).exists()
    ]
    if not files:
        print("no LAMP files found; run without --no-download", file=sys.stderr)
        return 1
    have = [f.name[:10] for f in files]
    con = duckdb.connect()
    load_ours(con, args.facts)
    con.execute(
        "delete from ours where cast(service_date as varchar) not in ("
        + ", ".join(f"'{d}'" for d in have)
        + ")"
    )
    load_mbta(con, files)
    n_after_cutoff = drop_after_lamp_cutoff(con)
    counters = match(con)
    text = report(con, counters, have, n_after_cutoff)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(text)
    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
