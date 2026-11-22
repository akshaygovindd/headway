import gzip
import hashlib
import json
from pathlib import Path

import pytest
from google.transit import gtfs_realtime_pb2 as pb

from headway.config import load_city_config
from headway.samples.build import build_sample
from headway.samples.expected import compute_expected, iter_entries, render_expected
from headway.samples.faults import parse_fault_counts

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "boston"
SOURCE = ROOT / ".local" / "capture-1h"
GTFS = ROOT / ".local" / "gtfs" / "MBTA_GTFS.zip"
CONFIG = load_city_config("boston")


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_sample_is_small() -> None:
    total = sum(p.stat().st_size for p in SAMPLE.rglob("*") if p.is_file())
    assert total < 20 * 1024 * 1024


def test_reparsing_reproduces_expected() -> None:
    expected = compute_expected(SAMPLE, CONFIG)
    committed = (SAMPLE / "EXPECTED.json").read_text()
    assert json.loads(committed) == expected
    assert committed == render_expected(expected)


def test_seeded_fault_counts_match_faults_md() -> None:
    documented = parse_fault_counts((SAMPLE / "FAULTS.md").read_text())
    entries = iter_entries(SAMPLE, CONFIG.storage_prefix)

    trip_updates = sorted(
        (e for e in entries if e.feed == "trip_updates"), key=lambda e: e.scheduled_at
    )
    duplicates = sum(
        1
        for a, b in zip(trip_updates, trip_updates[1:], strict=False)
        if a.content_hash == b.content_hash
    )
    schedule = sorted({e.scheduled_at for e in entries})
    missing = sum(
        int((b - a).total_seconds() // 60) - 1 for a, b in zip(schedule, schedule[1:], strict=False)
    )
    corrupt = sum(1 for e in entries if e.header_parse_error)

    outside = cleared = 0
    box = CONFIG.bbox
    for e in entries:
        if not e.s3_key or e.header_parse_error or e.feed == "alerts":
            continue
        feed = pb.FeedMessage.FromString(gzip.decompress((SAMPLE / e.s3_key).read_bytes()))
        for ent in feed.entity:
            if e.feed == "trip_updates" and not ent.trip_update.trip.trip_id:
                cleared += 1
            if e.feed == "vehicle_positions":
                p = ent.vehicle.position
                if not (
                    box.lat_min <= p.latitude <= box.lat_max
                    and box.lon_min <= p.longitude <= box.lon_max
                ):
                    outside += 1

    assert documented == {
        "corrupt_trip_updates": corrupt,
        "duplicate_capture": duplicates,
        "missing_minute": missing,
        "trip_id_cleared": cleared,
        "vehicle_out_of_bbox": outside,
    }
    assert documented == {
        "corrupt_trip_updates": 1,
        "duplicate_capture": 2,
        "missing_minute": 1,
        "trip_id_cleared": 2,
        "vehicle_out_of_bbox": 3,
    }


@pytest.mark.skipif(
    not (SOURCE.is_dir() and GTFS.is_file()), reason="original captures are not committed"
)
def test_build_twice_is_byte_identical(tmp_path: Path) -> None:
    for name in ("one", "two"):
        build_sample(CONFIG, SOURCE, GTFS, tmp_path / name)
    first, second = _hashes(tmp_path / "one"), _hashes(tmp_path / "two")
    assert first == second
    assert first == _hashes(SAMPLE)
