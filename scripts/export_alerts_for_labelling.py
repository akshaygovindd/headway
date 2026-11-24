"""Export distinct subway alerts for hand labelling (Release 3, C12 pilot).

Reads the curated ``alerts`` table, keeps one row per distinct (alert_id, header_text), draws about
``--n`` of them (round-robin over the GTFS effects so rare effects are present, seeded) and writes
``evals/alert_labels.csv`` with an empty ``label`` column. Label by hand with
evals/ALERT_LABELLING.md, then run ``make alert-classifier``.

    uv run python scripts/export_alerts_for_labelling.py --local .local/replay
    uv run python scripts/export_alerts_for_labelling.py --s3-bucket <raw bucket>   # read-only

The script never overwrites a file that already holds labels unless ``--force`` is given.
"""

import argparse
import csv
import hashlib
import io
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from headway.storage import LocalStorage, S3Storage, Storage  # noqa: E402

COLUMNS = ["alert_id", "effect", "cause", "route_ids", "header_text", "label"]
READ = ["alert_id", "effect", "cause", "route_id", "header_text"]


def read_alerts(storage: Storage, alerts_prefix: str) -> list[dict[str, Any]]:
    """Distinct (alert_id, header_text) rows with the lines they were seen on."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for key in storage.list_keys(alerts_prefix):
        if not key.endswith(".parquet"):
            continue
        table = pq.read_table(io.BytesIO(storage.get_bytes(key)), columns=READ)
        for row in table.to_pylist():
            header = (row["header_text"] or "").strip()
            if not header:
                continue
            item = seen.setdefault(
                (str(row["alert_id"]), header),
                {
                    "alert_id": str(row["alert_id"]),
                    "effect": row["effect"] or "",
                    "cause": row["cause"] or "",
                    "header_text": header,
                    "routes": set(),
                },
            )
            if row["route_id"]:
                item["routes"].add(row["route_id"])
    return list(seen.values())


def _order(item: dict[str, Any], seed: int) -> str:
    return hashlib.md5(f"{seed}:{item['alert_id']}:{item['header_text']}".encode()).hexdigest()


def sample(alerts: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """Round-robin over effects (seeded order inside each effect); at most n rows."""
    by_effect: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in sorted(alerts, key=lambda a: _order(a, seed)):
        by_effect[a["effect"]].append(a)
    queues = [by_effect[e] for e in sorted(by_effect)]
    out: list[dict[str, Any]] = []
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop(0))
    return sorted(out, key=lambda a: _order(a, seed + 1))


def has_labels(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open(newline="") as f:
        return any((r.get("label") or "").strip() for r in csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "alert_id": r["alert_id"],
                    "effect": r["effect"],
                    "cause": r["cause"],
                    "route_ids": ";".join(sorted(r["routes"])),
                    "header_text": r["header_text"],
                    "label": "",
                }
            )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--local", type=Path, help="storage root holding curated/<prefix>/table=alerts"
    )
    src.add_argument("--s3-bucket", help="bucket holding curated/ (read-only use)")
    p.add_argument("--prefix", default="boston")
    p.add_argument(
        "--alerts-prefix",
        help="key prefix of the alerts table (default curated/<prefix>/table=alerts/)",
    )
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", type=Path, default=ROOT / "evals" / "alert_labels.csv")
    p.add_argument("--force", action="store_true", help="overwrite a file that has labels")
    args = p.parse_args(argv)

    if has_labels(args.out) and not args.force:
        print(f"{args.out} already has labels; use --force to overwrite", file=sys.stderr)
        return 1
    storage: Storage = LocalStorage(args.local) if args.local else S3Storage(args.s3_bucket)
    alerts = read_alerts(storage, args.alerts_prefix or f"curated/{args.prefix}/table=alerts/")
    rows = sample(alerts, args.n, args.seed)
    write_csv(args.out, rows)
    print(f"{len(alerts)} distinct alerts; wrote {len(rows)} to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
