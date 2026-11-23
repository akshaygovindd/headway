"""Arrival-reconstruction sampling spike (Release 2, Phase 2.0). Local only, read-only.

    uv run python scripts/spike_arrivals.py --poll            # 10 min feed poll -> poll.json
    uv run python scripts/spike_arrivals.py                   # classify + hold-out -> results.json

Reads the curated Parquet copied into `--data-dir` (default .local/spike):
vehicle_positions/, static/{trips,stop_times,stops,shapes}/. Prints markdown tables.

Method (per Release 2 amendments):
* An observation is a de-duplicated vehicle_positions row, timed by its feed `event_ts`.
* Observed interval: last observation before the first STOPPED_AT at the stop -> that STOPPED_AT.
  Midpoint is the arrival estimate; confidence high when the interval is <= 30 s.
* Bracketed: no STOPPED_AT at the stop; interpolate by distance along the trip's shape between the
  last observation before and the first after. Medium when that gap is <= 120 s, else low.
* Missing: neither is possible (no observation before the stop, or none after).
* Observable trip-stops: stops of an observed trip that lie between its first and last observed
  position along the shape.
"""

import argparse
import json
import math
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx
from google.transit import gtfs_realtime_pb2

FEED_URL = "https://cdn.mbta.com/realtime/VehiclePositions.pb"
LINES = ["Red", "Orange", "Blue", "Green-B", "Green-C", "Green-D", "Green-E", "Mattapan"]
HIGH_WIDTH_S = 30.0
MEDIUM_GAP_S = 120.0
EARTH_M = 6_371_000.0


# --------------------------------------------------------------------------- feed poll


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    if not s:
        return float("nan")
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def dist_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "p90": pct(values, 0.9),
        "max": max(values),
    }


def poll(out: Path, minutes: float, every_s: float) -> None:
    """Fetch the vehicle feed every `every_s` seconds; summarise header and vehicle updates."""
    header_ts: list[int] = []  # one entry per fetch
    veh: dict[str, list[int]] = defaultdict(list)  # vehicle -> distinct event_ts, in order
    fetch_ms: list[float] = []
    end = time.monotonic() + minutes * 60
    next_at = time.monotonic()
    with httpx.Client(headers={"User-Agent": "headway-spike"}, timeout=10) as client:
        while time.monotonic() < end:
            t0 = time.monotonic()
            r = client.get(FEED_URL)
            r.raise_for_status()
            fetch_ms.append((time.monotonic() - t0) * 1000)
            msg = gtfs_realtime_pb2.FeedMessage()
            msg.ParseFromString(r.content)
            header_ts.append(int(msg.header.timestamp))
            for e in msg.entity:
                if not e.HasField("vehicle"):
                    continue
                v = e.vehicle
                route = v.trip.route_id
                if not (route in LINES or route.startswith("Green")):
                    continue
                ts = int(v.timestamp)
                vid = v.vehicle.id or e.id
                if not veh[vid] or veh[vid][-1] != ts:
                    veh[vid].append(ts)
            next_at += every_s
            time.sleep(max(0.0, next_at - time.monotonic()))
    changes = [b - a for a, b in zip(header_ts, header_ts[1:], strict=False) if b != a]
    vdeltas = [float(b - a) for ts in veh.values() for a, b in zip(ts, ts[1:], strict=False)]
    # Per-vehicle updates seen per 10 minutes, a rate view that ignores the 5 s poll grid.
    res: dict[str, Any] = {
        "polls": len(header_ts),
        "poll_every_s": every_s,
        "minutes": minutes,
        "header_changes": len(changes),
        "header_unchanged_polls": len(header_ts) - 1 - len(changes),
        "header_change_interval_s": dist_summary([float(c) for c in changes]),
        "vehicles": len(veh),
        "vehicle_update_interval_s": dist_summary(vdeltas),
        "fetch_ms": dist_summary(fetch_ms),
        "started": datetime.now(UTC).isoformat(),
    }
    out.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


# --------------------------------------------------------------------------- geometry


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(h))


class Shape:
    """Polyline with cumulative metres; projects points onto it."""

    def __init__(self, pts: list[tuple[float, float]]) -> None:
        self.pts = pts
        self.cum = [0.0]
        for a, b in zip(pts, pts[1:], strict=False):
            self.cum.append(self.cum[-1] + haversine(a, b))
        lat0 = math.radians(pts[0][0])
        self.kx = EARTH_M * math.cos(lat0) * math.pi / 180
        self.ky = EARTH_M * math.pi / 180
        self.xy = [(lon * self.kx, lat * self.ky) for lat, lon in pts]

    def project(
        self, lat: float, lon: float, lo: float = -1e18, hi: float = 1e18
    ) -> tuple[float, float]:
        """Return (distance along shape, offset metres), searching segments overlapping [lo, hi]."""
        px, py = lon * self.kx, lat * self.ky
        best = (float("inf"), 0.0)
        for i in range(len(self.xy) - 1):
            if self.cum[i + 1] < lo or self.cum[i] > hi:
                continue
            ax, ay = self.xy[i]
            bx, by = self.xy[i + 1]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
            d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d < best[0]:
                best = (d, self.cum[i] + t * (self.cum[i + 1] - self.cum[i]))
        return best[1], best[0]


# --------------------------------------------------------------------------- analysis


class Obs:
    __slots__ = ("seq", "status", "stop_id", "t")

    def __init__(self, t: float, status: str, seq: int | None, stop_id: str | None) -> None:
        self.t, self.status, self.seq, self.stop_id = t, status, seq, stop_id


def bracket(
    s: list[float], t: list[float], target: float, skip: set[int] | None = None
) -> tuple[float, float] | None:
    """Interpolate the time at which distance `target` was reached. Returns (time, gap_s)."""
    skip = skip or set()
    before = after = None
    for i, d in enumerate(s):
        if i in skip:
            continue
        if d < target:
            before = i
        elif after is None:
            after = i
    if before is None or after is None or t[after] < t[before]:
        return None
    ds = s[after] - s[before]
    frac = 0.0 if ds <= 0 else (target - s[before]) / ds
    return t[before] + frac * (t[after] - t[before]), t[after] - t[before]


def analyse(data_dir: Path) -> dict[str, Any]:
    con = duckdb.connect()
    vp = f"read_parquet('{data_dir}/vehicle_positions/**/*.parquet')"
    st = f"'{data_dir}/static"
    rows = con.sql(
        f"""
        SELECT DISTINCT vehicle_id, trip_id, route_id, lat, lon, current_status, stop_id,
               current_stop_sequence, epoch(event_ts) AS t
        FROM {vp}
        WHERE trip_id IS NOT NULL AND event_ts IS NOT NULL AND NOT is_stale_position
        ORDER BY vehicle_id, trip_id, t
        """
    ).fetchall()
    trip_ids = sorted({r[1] for r in rows})
    ids = ",".join("'" + x.replace("'", "''") + "'" for x in trip_ids)
    trips = {
        r[0]: r[1]
        for r in con.sql(
            f"SELECT trip_id, shape_id FROM read_parquet({st}/trips/**/*.parquet') "
            f"WHERE trip_id IN ({ids})"
        ).fetchall()
    }
    stop_times: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for tid, seq, sid in con.sql(
        f"SELECT trip_id, stop_sequence, stop_id FROM read_parquet({st}/stop_times/**/*.parquet') "
        f"WHERE trip_id IN ({ids}) ORDER BY trip_id, stop_sequence"
    ).fetchall():
        stop_times[tid].append((seq, sid))
    stops = {
        r[0]: (r[1], r[2])
        for r in con.sql(
            f"SELECT stop_id, stop_lat, stop_lon FROM read_parquet({st}/stops/**/*.parquet')"
        ).fetchall()
    }
    shape_ids = sorted({s for s in trips.values() if s})
    sids = ",".join("'" + x + "'" for x in shape_ids)
    raw_shapes: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for sid, la, lo in con.sql(
        f"SELECT shape_id, shape_pt_lat, shape_pt_lon FROM read_parquet({st}/shapes/**/*.parquet') "
        f"WHERE shape_id IN ({sids}) ORDER BY shape_id, shape_pt_sequence"
    ).fetchall():
        raw_shapes[sid].append((la, lo))
    shapes = {k: Shape(v) for k, v in raw_shapes.items() if len(v) > 1}

    # group observations per (trip, vehicle)
    groups: dict[tuple[str, str], list[tuple[Any, ...]]] = defaultdict(list)
    for r in rows:
        groups[(r[1], r[0])].append(r)

    counts = {
        "trip_vehicle_groups": len(groups),
        "trips_not_in_static": 0,
        "groups_lt2_obs": 0,
        "observations": len(rows),
    }
    cls_counts: dict[str, dict[str, int]] = {ln: defaultdict(int) for ln in LINES}
    open_start: dict[str, int] = defaultdict(int)  # STOPPED_AT with no prior observation
    holdout: dict[str, dict[str, list[float]]] = {ln: {"abs": [], "signed": []} for ln in LINES}
    holdout_skipped: dict[str, int] = defaultdict(int)
    widths: dict[str, list[float]] = {ln: [] for ln in LINES}

    for (trip_id, _vid), grp in groups.items():
        line = grp[0][2]
        if line not in cls_counts:
            continue
        sh = shapes.get(trips.get(trip_id) or "")
        stl = stop_times.get(trip_id)
        if sh is None or not stl:
            counts["trips_not_in_static"] += 1
            continue
        if len(grp) < 2:
            counts["groups_lt2_obs"] += 1
            continue
        # distance along the shape of each scheduled stop, forced non-decreasing
        stop_s: dict[int, float] = {}
        prev = 0.0
        for seq, sid in stl:
            if sid not in stops:
                continue
            la, lo = stops[sid]
            d, _ = sh.project(la, lo, lo=prev - 50, hi=1e18)
            prev = max(prev, d)
            stop_s[seq] = prev
        # observation positions, windowed + monotone
        obs: list[Obs] = []
        s_list: list[float] = []
        prev_s: float | None = None
        for r in grp:
            _, _, _, la, lo, status, stop_id, seq, t = r
            if status == "STOPPED_AT" and seq in stop_s:
                d = stop_s[seq]
            elif prev_s is None:
                d, _ = sh.project(la, lo)
            else:
                d, off = sh.project(la, lo, lo=prev_s - 300, hi=prev_s + 4000)
                if off > 500:
                    d, _ = sh.project(la, lo)
            if prev_s is not None:
                d = max(d, prev_s)
            prev_s = d
            obs.append(Obs(t, status, seq, stop_id))
            s_list.append(d)
        t_list = [o.t for o in obs]
        lo_s, hi_s = s_list[0], s_list[-1]
        for seq, _sid in stl:
            if seq not in stop_s:
                continue
            target = stop_s[seq]
            if not (lo_s <= target <= hi_s):
                continue  # not observable: outside the observed stretch
            stopped = [i for i, o in enumerate(obs) if o.status == "STOPPED_AT" and o.seq == seq]
            if stopped:
                i1 = stopped[0]
                if i1 == 0:
                    open_start[line] += 1
                    cls_counts[line]["missing"] += 1
                    continue
                width = obs[i1].t - obs[i1 - 1].t
                widths[line].append(width)
                if width <= HIGH_WIDTH_S:
                    cls_counts[line]["observed_high"] += 1
                else:
                    cls_counts[line]["observed_wide"] += 1
                # hold-out: drop every STOPPED_AT capture at this stop, reconstruct by bracketing
                midpoint = (obs[i1].t + obs[i1 - 1].t) / 2
                rec = bracket(s_list, t_list, target, skip=set(stopped))
                if rec is None:
                    holdout_skipped[line] += 1
                else:
                    err = rec[0] - midpoint
                    holdout[line]["abs"].append(abs(err))
                    holdout[line]["signed"].append(err)
                continue
            rec = bracket(s_list, t_list, target)
            if rec is None:
                cls_counts[line]["missing"] += 1
            elif rec[1] <= MEDIUM_GAP_S:
                cls_counts[line]["bracketed_medium"] += 1
            else:
                cls_counts[line]["bracketed_low"] += 1

    return {
        "counts": counts,
        "classes": {k: dict(v) for k, v in cls_counts.items()},
        "open_start": dict(open_start),
        "observed_width_s": {k: dist_summary(v) for k, v in widths.items()},
        "holdout": {
            ln: {
                "n": len(h["abs"]),
                "skipped": holdout_skipped.get(ln, 0),
                "median_abs_s": statistics.median(h["abs"]) if h["abs"] else None,
                "p90_abs_s": pct(h["abs"], 0.9) if h["abs"] else None,
                "median_signed_s": statistics.median(h["signed"]) if h["signed"] else None,
                "within_30s": sum(x <= 30 for x in h["abs"]) / len(h["abs"]) if h["abs"] else None,
                "within_60s": sum(x <= 60 for x in h["abs"]) / len(h["abs"]) if h["abs"] else None,
            }
            for ln, h in holdout.items()
        },
    }


def fmt(x: float | None, nd: int = 1) -> str:
    return "-" if x is None else f"{x:.{nd}f}"


def tables(res: dict[str, Any]) -> str:
    out = ["", "### Classification shares (observable trip-stops)", ""]
    out.append(
        "| Line | Trip-stops | Observed high | Observed wide | Bracketed medium "
        "| Bracketed low | Missing |"
    )
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    keys = ["observed_high", "observed_wide", "bracketed_medium", "bracketed_low", "missing"]
    tot: dict[str, int] = defaultdict(int)
    for ln in LINES:
        c = res["classes"][ln]
        n = sum(c.values())
        for k in keys:
            tot[k] += c.get(k, 0)
        cells = " | ".join(f"{100 * c.get(k, 0) / n:.1f}%" if n else "-" for k in keys)
        out.append(f"| {ln} | {n:,} | {cells} |")
    n = sum(tot.values())
    out.append(f"| All | {n:,} | " + " | ".join(f"{100 * tot[k] / n:.1f}%" for k in keys) + " |")
    out += ["", "### Hold-out error (reconstruction vs observed-interval midpoint)", ""]
    out.append(
        "| Line | n | Median abs (s) | p90 abs (s) | Median signed (s) "
        "| Within 30 s | Within 60 s |"
    )
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    for ln in LINES:
        h = res["holdout"][ln]
        w30 = fmt(None if h["within_30s"] is None else 100 * h["within_30s"]) + "%"
        w60 = fmt(None if h["within_60s"] is None else 100 * h["within_60s"]) + "%"
        out.append(
            f"| {ln} | {h['n']:,} | {fmt(h['median_abs_s'])} | {fmt(h['p90_abs_s'])} | "
            f"{fmt(h['median_signed_s'])} | {w30} | {w60} |"
        )
    out += [
        "",
        "### Observed-interval width (s)",
        "",
        "| Line | n | Median | p90 |",
        "|---|---:|---:|---:|",
    ]
    for ln in LINES:
        w = res["observed_width_s"][ln]
        out.append(f"| {ln} | {w['n']:,} | {fmt(w.get('median'))} | {fmt(w.get('p90'))} |")
    out += [
        "",
        f"Counts: {json.dumps(res['counts'])}; STOPPED_AT with no earlier capture "
        f"(counted missing): {json.dumps(res['open_start'])}",
    ]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=Path(".local/spike"))
    ap.add_argument("--poll", action="store_true", help="poll the live feed instead of analysing")
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--every", type=float, default=5)
    a = ap.parse_args()
    a.data_dir.mkdir(parents=True, exist_ok=True)
    if a.poll:
        poll(a.data_dir / "poll.json", a.minutes, a.every)
        return
    res = analyse(a.data_dir)
    (a.data_dir / "results.json").write_text(json.dumps(res, indent=2))
    print(tables(res))


if __name__ == "__main__":
    main()
