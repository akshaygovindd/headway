# Boston subway sample

A small, committed slice of real MBTA data, used for tests and local replay.

- Source: 59 one-minute capture runs of the MBTA realtime feeds, taken on Sun 22 Nov 2026
  (a Sunday morning, 14:36 to 15:35 UTC), plus the MBTA GTFS static feed.
- Subway only: routes Red, Orange, Blue, Green-B, Green-C, Green-D, Green-E, Mattapan. Trip updates and vehicle positions are kept when their
  route is one of these; alerts are kept when any informed entity is one of these.
  Production raw storage is unfiltered; this filtering exists only to keep the sample small.
- `raw/` and `manifests/` follow the production key layout. Feed headers are preserved;
  content hashes and capture ids are recomputed from the filtered bytes.
- `gtfs/` holds the static subset: subway routes, trips whose service runs on Sun 22 Nov 2026,
  their stop times, referenced stops and parent stations, shapes, calendars, agency, feed info.
- `FAULTS.md` lists the deliberately seeded faults (seed 20261122); everything else is unmodified.
- `EXPECTED.json` holds the counts that parsing and validating this sample must reproduce.

Rebuild (needs the original captures and the GTFS archive, which are not committed):

    headway samples build --source .local/capture-1h --gtfs .local/gtfs/MBTA_GTFS.zip \
        --out samples/boston --city boston

The build is deterministic: the same inputs produce byte-identical output.
