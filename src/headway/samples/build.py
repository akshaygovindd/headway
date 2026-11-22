"""Build the committed sample dataset from a real capture and a GTFS archive."""

import shutil
from pathlib import Path

from headway.config import CityConfig
from headway.parse.gtfs_static import GtfsSource
from headway.samples.captures import Run, load_runs, rebuild, write_runs
from headway.samples.expected import compute_expected, render_expected
from headway.samples.faults import SEED, apply_faults, render_faults
from headway.samples.filter import filter_feed
from headway.samples.gtfs_subset import build_gtfs_subset
from headway.timeutil import service_date_for

README = """# Boston subway sample

A small, committed slice of real MBTA data, used for tests and local replay.

- Source: {runs} one-minute capture runs of the MBTA realtime feeds, taken on {day}
  (a Sunday morning, {first} to {last} UTC), plus the MBTA GTFS static feed.
- Subway only: routes {routes}. Trip updates and vehicle positions are kept when their
  route is one of these; alerts are kept when any informed entity is one of these.
  Production raw storage is unfiltered; this filtering exists only to keep the sample small.
- `raw/` and `manifests/` follow the production key layout. Feed headers are preserved;
  content hashes and capture ids are recomputed from the filtered bytes.
- `gtfs/` holds the static subset: subway routes, trips whose service runs on {day},
  their stop times, referenced stops and parent stations, shapes, calendars, agency, feed info.
- `FAULTS.md` lists the deliberately seeded faults (seed {seed}); everything else is unmodified.
- `EXPECTED.json` holds the counts that parsing and validating this sample must reproduce.

Rebuild (needs the original captures and the GTFS archive, which are not committed):

    headway samples build --source .local/capture-1h --gtfs .local/gtfs/MBTA_GTFS.zip \\
        --out samples/boston --city boston

The build is deterministic: the same inputs produce byte-identical output.
"""


def build_sample(config: CityConfig, source: Path, gtfs: Path, out: Path) -> dict[str, int]:
    prefix = config.storage_prefix
    routes = frozenset(config.lines)
    runs = load_runs(source, prefix)
    if not runs:
        raise ValueError(f"no capture manifests found under {source}")

    filtered: list[Run] = []
    for run in runs:
        run2 = run
        for cap in run.captures:
            if cap.body is not None:
                new = rebuild(cap.entry, filter_feed(cap.entry.feed, cap.body, routes), prefix)
                run2 = run2.replace_capture(cap.entry.feed, new)
        filtered.append(run2)

    days = sorted(
        {
            service_date_for(c.entry.fetched_at, config.timezone, config.service_day_cutoff_hour)
            for run in runs
            for c in run.captures
        }
    )
    faulted, faults = apply_faults(filtered, prefix, config.bbox)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    write_runs(out, prefix, faulted)
    counts = build_gtfs_subset(
        GtfsSource(gtfs), out / "gtfs", routes, frozenset(config.route_types), days
    )
    (out / "FAULTS.md").write_text(render_faults(faults))
    first, last = faulted[0].scheduled_at, faulted[-1].scheduled_at
    (out / "README.md").write_text(
        README.format(
            runs=len(faulted),
            day=", ".join(f"{d:%a %d %b %Y}" for d in days),
            first=f"{first:%H:%M}",
            last=f"{last:%H:%M}",
            routes=", ".join(config.lines),
            seed=SEED,
        )
    )
    (out / "EXPECTED.json").write_text(render_expected(compute_expected(out, config)))
    return counts
