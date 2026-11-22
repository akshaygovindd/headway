# Lineage

Written during Release 1.

## Raw to curated to quarantine

Every object lives under a city prefix (`boston` below). Keys are UTC.

| Layer | Key | Written by |
| --- | --- | --- |
| raw | `raw/{prefix}/feed={feed}/dt=YYYY-MM-DD/hour=HH/{capture_id}.pb.gz` | ingest |
| manifest | `manifests/{prefix}/dt=YYYY-MM-DD/hour=HH/{run_id}.json` | ingest |
| curated | `curated/{prefix}/table={table}/dt=YYYY-MM-DD/hour=HH/part-{batch_id}.parquet` | `normalize_batch` |
| compacted | `curated/{prefix}/table={table}/dt=YYYY-MM-DD/hour=HH/part-compacted.parquet` | `compact_hour` |
| static | `curated/{prefix}/static/table={table}/feed_version={v}/part.parquet` | `load_static` |
| quarantine | `quarantine/{prefix}/table={table}/dt=YYYY-MM-DD/{capture_id}.jsonl` | `normalize_batch` |
| watermark | `state/{prefix}/curate_watermark.json` | `curate_new` |

- **Raw is never modified or deleted.** Curation only reads it, so any curated or quarantine
  object can be rebuilt by replaying raw and manifests (`headway replay`).
- **Curated** rows are partitioned by the capture's `fetched_at`. Realtime tables
  (`trip_updates`, `vehicle_positions`, `alerts`) carry `capture_id`, `run_id`, `city_id`,
  `content_hash`, `fetched_at` and `feed_header_ts`. Captures are never deduplicated; captures
  with identical content but different `capture_id` all appear, and `content_hash` lets
  downstream models flag them. The same `capture_id` in one batch is written once.
- **Quarantine** holds one JSON line per rejected row with `capture_id`, `reason`, `error` and the
  original row. Reasons: `schema_error`, `missing_trip_id`, `missing_stop_id`,
  `invalid_coordinates`, `timestamp_out_of_range`, `unknown_route` (`route_id` not among the
  current routes of the city's route types) and `protobuf_parse_error` (one line for the whole
  capture; its raw object stays in place).
- **Batches** are the manifest entries of one fetched-at hour. `batch_id` is the first 16 hex
  characters of the SHA-256 of the sorted `capture_id`s, so reprocessing the same captures
  overwrites the same part file.
- **Watermark** records the latest `fetched_at` processed and the `capture_id`s seen at that
  instant. It moves only after all writes of a batch succeeded; a failed run is simply retried.
- **Compaction** merges an hour's `part-*.parquet` into `part-compacted.parquet`, deleting the
  inputs only after the merged row count equals the sum of the inputs. Re-running it is a no-op.
- **Static** tables are loaded once per `feed_version` (path-safe form in the key, raw value in
  the `feed_version` column); an existing version is skipped.
