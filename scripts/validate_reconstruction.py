"""Cross-check reconstructed arrivals against MBTA's LAMP subway on-time-performance files.

Source: https://performancedata.mbta.com/lamp/subway-on-time-performance-v1/ (daily Parquet, one row
per trip_id x stop_id x service_date). Use is governed by the MassDOT Developers License Agreement
(use, reproduce and redistribute, with MassDOT acknowledged as the provider); see
docs/VALIDATION.md. The files are downloaded to .local/validation/lamp (git-ignored) and never
committed. This is not part of `make check` or CI because it needs a download.

    make validate
    uv run python scripts/validate_reconstruction.py --download-only
"""

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://performancedata.mbta.com/lamp/subway-on-time-performance-v1"
DEFAULT_LAMP_DIR = ROOT / ".local" / "validation" / "lamp"
USER_AGENT = "headway-validation/1"


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--days", nargs="+", default=["2026-11-22", "2026-11-23"], metavar="YYYY-MM-DD")
    ap.add_argument("--lamp-dir", type=Path, default=DEFAULT_LAMP_DIR)
    ap.add_argument("--download-only", action="store_true")
    args = ap.parse_args()
    manifest = download_days(args.days, args.lamp_dir)
    return 0 if manifest else 1


if __name__ == "__main__":
    sys.exit(main())
