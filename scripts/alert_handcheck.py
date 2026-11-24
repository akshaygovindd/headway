"""Export a sample of incident/alert matches to CSV for a manual review (Release 3, C13).

The matching (int_alert_matching) is a rule: same line, time overlap within +-30 minutes. Whether it
is right is a judgement this script prepares and a person makes: it writes at least `--per-kind`
rows of each of the four kinds (matched incident, unmatched incident, matched alert, unmatched
alert) with what is needed to decide, and empty reviewer columns. Record the outcome in
docs/MEASUREMENTS.md ("Alert vs reality: hand check").

    uv run python scripts/alert_handcheck.py --db .local/demo-r2/headway.duckdb --out handcheck.csv

--db is a DuckDB file from a dbt build with HEADWAY_ENABLE_R3=true (the alert rows are not in the
public gold tables). The sample is deterministic: ordered by a hash of the id and --seed.
"""

import argparse
import csv
import sys
from pathlib import Path

import duckdb

KINDS = (
    ("incident", "matched"),
    ("incident", "unmatched_incident"),
    ("alert", "matched"),
    ("alert", "unmatched_alert"),
)
COLUMNS = [
    "kind", "match_status", "item_id", "line", "event_utc", "window_or_alert_utc", "headline",
    "counterparts", "alert_lag_s", "reviewer_verdict", "reviewer_note",
]  # fmt: skip

INCIDENTS = """
select
    'incident' as kind, m.match_status, m.item_id, m.line,
    strftime(i.incident_start_ts at time zone 'UTC', '%Y-%m-%d %H:%M') as event_utc,
    strftime(i.incident_start_ts at time zone 'UTC', '%H:%M') || ' to '
        || strftime(i.incident_end_ts at time zone 'UTC', '%H:%M') as window_or_alert_utc,
    coalesce(a.summary_text, 'rules: ' || i.thresholds_crossed) as headline,
    coalesce(m.counterpart_ids, '') as counterparts,
    m.alert_lag_s
from int_alert_matching m
join fct_incidents i on i.incident_id = m.item_id
left join agg_incidents a on a.incident_id = i.incident_id
where m.kind = 'incident' and m.match_status = ?
order by md5(m.item_id || ?) limit ?
"""
ALERTS = """
select
    'alert' as kind, m.match_status, m.item_id, m.line,
    strftime(m.event_ts at time zone 'UTC', '%Y-%m-%d %H:%M') as event_utc,
    strftime(a.first_seen at time zone 'UTC', '%H:%M') || ' to '
        || strftime(a.last_seen at time zone 'UTC', '%H:%M') as window_or_alert_utc,
    a.effect || ': ' || coalesce(a.header_text, '') as headline,
    coalesce(m.counterpart_ids, '') as counterparts,
    cast(null as double) as alert_lag_s
from int_alert_matching m
join (
    select line, alert_id, any_value(effect) as effect, any_value(header_text) as header_text,
        min(first_seen) as first_seen, max(last_seen) as last_seen
    from int_alert_scopes where scope = 'line' and is_service_disruption group by 1, 2
) a on a.line = m.line and a.alert_id = m.item_id
where m.kind = 'alert' and m.match_status = ?
order by md5(m.item_id || ?) limit ?
"""


def sample(db: Path, per_kind: int, seed: str) -> tuple[list[list[object]], dict[str, int]]:
    """Rows to review and how many each kind has."""
    con = duckdb.connect(str(db), read_only=True)
    con.execute("set TimeZone = 'UTC'")
    rows: list[list[object]] = []
    found: dict[str, int] = {}
    try:
        for kind, status in KINDS:
            query = INCIDENTS if kind == "incident" else ALERTS
            part = con.execute(query, [status, seed, per_kind]).fetchall()
            found[f"{kind}/{status}"] = len(part)
            rows += [[*r, "", ""] for r in part]
    finally:
        con.close()
    return rows, found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("alert_handcheck.csv"))
    parser.add_argument("--per-kind", type=int, default=12, help="rows per kind (at least 10)")
    parser.add_argument("--seed", default="headway")
    args = parser.parse_args(argv)
    if args.per_kind < 10:
        parser.error("--per-kind must be at least 10")
    rows, found = sample(args.db, args.per_kind, args.seed)
    with args.out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    for key, n in found.items():
        note = (
            "" if n >= 10 else "  <- fewer than 10 available: collect more days before the review"
        )
        print(f"{key:28s} {n:3d}{note}")
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
