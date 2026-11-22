"""Seeded faults applied to a filtered sample, with a report of exactly what was changed."""

import random
from dataclasses import dataclass

from google.transit import gtfs_realtime_pb2

from headway.config import BBox
from headway.parse.gtfs_rt import ParseError, parse_header
from headway.samples.captures import Run, rebuild

SEED = 20261122

OUTSIDE_POINTS = [(0.0, 0.0), (51.5074, -0.1278), (48.8566, 2.3522)]


@dataclass(frozen=True)
class Fault:
    kind: str
    run_id: str
    capture_id: str
    detail: str


def _spread_indices(rng: random.Random, n_runs: int, count: int) -> list[int]:
    """Pick ``count`` interior run indices, no two adjacent, in a seeded order."""
    pool = list(range(1, n_runs - 1))
    rng.shuffle(pool)
    chosen: list[int] = []
    for idx in pool:
        if all(abs(idx - c) > 1 for c in chosen):
            chosen.append(idx)
        if len(chosen) == count:
            return chosen
    raise ValueError("not enough runs to place seeded faults")


def _decode(data: bytes) -> gtfs_realtime_pb2.FeedMessage:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(data)
    return feed


def _body(run: Run, feed: str) -> bytes:
    cap = run.capture(feed)
    if cap is None or cap.body is None:
        raise ValueError(f"run {run.run_id} has no {feed} body")
    return cap.body


def _set(run: Run, feed: str, body: bytes, prefix: str) -> tuple[Run, str]:
    cap = run.capture(feed)
    assert cap is not None
    new = rebuild(cap.entry, body, prefix)
    return run.replace_capture(feed, new), new.entry.capture_id


def apply_faults(runs: list[Run], prefix: str, bbox: BBox) -> tuple[list[Run], list[Fault]]:
    """Return runs with 2 duplicates, 1 missing minute, 1 corrupt trip_updates,
    3 out-of-bbox vehicles and 2 cleared trip ids; plus the fault list."""
    rng = random.Random(SEED)
    idx = _spread_indices(rng, len(runs), 9)
    dup_idx, missing_idx, corrupt_idx = idx[0:2], idx[2], idx[3]
    veh_idx, trip_idx = idx[4:7], idx[7:9]
    out = list(runs)
    faults: list[Fault] = []

    for i in dup_idx:
        previous = _body(runs[i - 1], "trip_updates")
        out[i], cid = _set(runs[i], "trip_updates", previous, prefix)
        faults.append(
            Fault("duplicate_capture", runs[i].run_id, cid, f"bytes of run {runs[i - 1].run_id}")
        )

    cut_run = runs[corrupt_idx]
    body = _body(cut_run, "trip_updates")
    cut = rng.randint(len(body) // 4, len(body) * 3 // 4)
    while True:
        try:
            parse_header(body[:cut])
        except ParseError:
            break
        cut += 1
    out[corrupt_idx], cid = _set(cut_run, "trip_updates", body[:cut], prefix)
    faults.append(
        Fault("corrupt_trip_updates", cut_run.run_id, cid, f"truncated {len(body)} -> {cut} bytes")
    )

    for i in veh_idx:
        feed = _decode(_body(runs[i], "vehicle_positions"))
        candidates = [
            e for e in feed.entity if e.HasField("vehicle") and e.vehicle.HasField("position")
        ]
        entity = candidates[rng.randrange(len(candidates))]
        lat, lon = OUTSIDE_POINTS[rng.randrange(len(OUTSIDE_POINTS))]
        assert not (bbox.lat_min <= lat <= bbox.lat_max and bbox.lon_min <= lon <= bbox.lon_max)
        entity.vehicle.position.latitude = lat
        entity.vehicle.position.longitude = lon
        out[i], cid = _set(
            runs[i], "vehicle_positions", feed.SerializeToString(deterministic=True), prefix
        )
        faults.append(
            Fault(
                "vehicle_out_of_bbox",
                runs[i].run_id,
                cid,
                f"vehicle {entity.vehicle.vehicle.id} -> ({lat}, {lon})",
            )
        )

    for i in trip_idx:
        feed = _decode(_body(runs[i], "trip_updates"))
        candidates = [
            e for e in feed.entity if e.HasField("trip_update") and e.trip_update.trip.trip_id
        ]
        entity = candidates[rng.randrange(len(candidates))]
        stops = len(entity.trip_update.stop_time_update)
        old = entity.trip_update.trip.trip_id
        entity.trip_update.trip.ClearField("trip_id")
        out[i], cid = _set(
            runs[i], "trip_updates", feed.SerializeToString(deterministic=True), prefix
        )
        faults.append(
            Fault(
                "trip_id_cleared",
                runs[i].run_id,
                cid,
                f"entity {entity.id} (was trip {old}), {stops} stop updates",
            )
        )

    removed = out[missing_idx]
    faults.append(
        Fault(
            "missing_minute",
            removed.run_id,
            "-",
            f"scheduled {removed.scheduled_at:%Y-%m-%dT%H:%M:%SZ}",
        )
    )
    del out[missing_idx]
    return out, faults


def render_faults(faults: list[Fault]) -> str:
    lines = [
        "# Seeded faults",
        "",
        f"Applied after subway filtering with seed `{SEED}`; nothing else is altered.",
        "",
        "| kind | run_id | capture_id | detail |",
        "| --- | --- | --- | --- |",
    ]
    lines += [f"| {f.kind} | {f.run_id} | {f.capture_id} | {f.detail} |" for f in faults]
    counts: dict[str, int] = {}
    for f in faults:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    lines += ["", "## Counts", ""]
    lines += [f"- {kind}: {n}" for kind, n in sorted(counts.items())]
    return "\n".join(lines) + "\n"


def parse_fault_counts(text: str) -> dict[str, int]:
    """Read per-kind counts back from rendered FAULTS.md table rows."""
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("| ") and not line.startswith(("| kind", "| ---")):
            kind = line.split("|")[1].strip()
            counts[kind] = counts.get(kind, 0) + 1
    return counts
