# Lineage

Written during Release 1.

## End to end

```mermaid
flowchart LR
    subgraph MBTA["MBTA feeds"]
        F1[TripUpdates]
        F2[VehiclePositions]
        F3[Alerts]
        F4[GTFS static zip]
    end

    subgraph INGEST["Ingest (Lambda, every minute)"]
        I1[fetch + gzip]
        I2[claim minute:<br/>conditional manifest write]
    end

    subgraph S3RAW["S3 raw bucket"]
        R1[(raw captures<br/>never modified)]
        R2[(manifests JSON<br/>UTC, run_id)]
        R3[(duplicates/<br/>delivery records)]
    end

    subgraph CURATE["Curate (Lambda, every 15 min)"]
        C1{{"contracts:<br/>schema + range checks"}}
        C2[out-of-scope<br/>route filter]
        C3[(curated Parquet<br/>incl. manifests table)]
        Q[(quarantine<br/>Parquet + JSONL)]
    end

    subgraph TRANSFORM["Transform (Lambda, hourly :20)"]
        T1[dbt on DuckDB<br/>staging, intermediate, marts]
        T2{{"dbt tests +<br/>hourly quality checks"}}
    end

    G[(gold Parquet<br/>public S3)]
    MD[(MotherDuck)]
    APP[Streamlit app]
    LIVE[(live snapshot JSON)]

    F1 & F2 & F3 --> I1 --> I2
    I1 --> R1
    I2 --> R2
    I2 -. lost minute .-> R3
    I1 --> LIVE
    F4 --> C1
    R1 --> C1
    R2 --> C1
    R3 --> C3
    C1 -- valid --> C2 --> C3
    C1 -- rejected --> Q
    C3 --> T1
    Q --> T1
    T1 --> T2 --> G
    G --> MD
    G --> APP
    LIVE --> APP

    REPLAY(["replay: rebuild curated,<br/>quarantine from raw + manifests"]) -.-> C3
    R1 -.-> REPLAY
    ALARMS["CloudWatch alarms -> SNS email:<br/>function errors, ingest invocations low,<br/>transform not succeeded"] -.-> INGEST
    ALARMS -.-> CURATE
    ALARMS -.-> TRANSFORM
```

Where each control sits:

| Control | Where | What it does |
| --- | --- | --- |
| Validation | curate (`contracts`), dbt tests | Pydantic contracts reject rows with a bad schema, ids, coordinates or timestamps. dbt tests check grain, keys, accepted ranges and descriptions. |
| Quarantine | curate | Rejected rows are written with `capture_id`, `reason` and the original row, then counted in `agg_pipeline_health` and the hourly checks. Out-of-scope routes are filtered and counted, not quarantined. |
| Duplicate handling | ingest, curate, dbt | Lost-minute deliveries are recorded under `manifests/<city>/duplicates/` and flagged `duplicate_delivery`, never dropped (ADR-013). Identical payloads are flagged `is_duplicate_capture` (ADR-005). |
| Monitoring and alarms | CloudWatch, `agg_quality_checks_hourly` | Alarms on errors of every function, on too few ingest invocations and on a transform that did not succeed; hourly quality checks and the Pipeline health page show coverage, missed runs and quarantine. |
| Replay | `headway replay`, `backfill_manifests` | Raw captures and manifests are never modified, so curated, quarantine and manifest tables can be rebuilt deterministically (batch ids are content hashes). |

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
| raw static | `raw/{prefix}/feed=gtfs_static/dt=YYYY-MM-DD/hour=HH/gtfs-{timestamp}.zip` | static Lambda |
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
  `invalid_coordinates`, `timestamp_out_of_range`, `unknown_route` (`route_id` absent from the
  current GTFS routes) and `protobuf_parse_error` (one line for the whole capture; its raw object
  stays in place).
- **Scope filter.** Raw captures cover every mode. Rows whose route exists in GTFS but whose
  `route_type` is not in the city's `route_types` are dropped, not quarantined, and counted per
  table as `filtered_out_of_scope`. Rows without a `route_id` (stop-only or agency-wide alerts)
  are kept.
- **Batches** are the manifest entries of one fetched-at hour. `batch_id` is the first 16 hex
  characters of the SHA-256 of the sorted `capture_id`s, so reprocessing the same captures
  overwrites the same part file.
- **Watermark** records the latest `fetched_at` processed and the `capture_id`s seen at that
  instant. It moves only after all writes of a batch succeeded; a failed run is simply retried.
- **Compaction** merges an hour's `part-*.parquet` into `part-compacted.parquet`, deleting the
  inputs only after the merged row count equals the sum of the inputs. Re-running it is a no-op.
- **Static** tables are loaded once per `feed_version` (path-safe form in the key, raw value in
  the `feed_version` column); an existing version is skipped.

## Curated to gold (AWS)

| Step | Function | Schedule (UTC) | Reads | Writes |
| --- | --- | --- | --- | --- |
| curate | `headway-curate` | every 15 minutes | manifests, raw | curated, quarantine, state |
| compact | `headway-curate` with `{"task":"compact"}` | `:05` hourly | previous hour's parts | `part-compacted.parquet` |
| static | `headway-static` | daily 09:00 | `MBTA_GTFS.zip` | raw static, curated static |
| transform | `headway-transform` | `:20` hourly | curated, manifests, quarantine (last `lookback_days`) | gold, MotherDuck |

- **Gold** lives at `s3://headway-public-<account>/public/{prefix}/gold/` as the dbt allowlist
  tables, `manifest.json` (row counts, `dbt_run_at`, `metrics_as_of`) and `cost_daily.parquet`.
  The Streamlit app reads them through `HEADWAY_DATA_URL`.
- **Lookback.** Staging models of realtime sources (`trip_updates`, `vehicle_positions`,
  `alerts`, manifests, quarantine) keep rows whose `dt` partition is within `lookback_days` of
  today (UTC). Static GTFS tables are not filtered. Local and replay builds leave the variable
  unset and read everything.
- **`cost_daily`** is written by the transform Lambda once per UTC day (first run at or after
  06:00): one row per day and AWS service for resources tagged `project=headway`, with the day
  total, capture count from `agg_pipeline_health`, and `cost_per_1000_captures`. It is not a dbt
  model; the app treats a missing file as "no cost data yet".
- **MotherDuck** holds a copy of each gold table (and `cost_daily`), replaced on every run.

## Release 2: reconstruction, metrics and windows

All models below are tagged `r2`; the transform Lambda builds them only when
`HEADWAY_ENABLE_R2=true`. Rows of `vehicle_positions` are sampled every 15 s (ADR-014).

```mermaid
flowchart LR
    VP[(vehicle_positions<br/>curated, 15 s)] --> SE[int_vehicle_stop_events<br/>trip match, shape distance,<br/>segments per reused trip id]
    SHAPES[(static: shapes,<br/>stop_shape_positions)] --> TSD[int_trip_stop_distances]
    TSD --> RA
    SE --> RA[fct_reconstructed_arrivals<br/>observed / bracketed / missing,<br/>confidence high / medium / low]
    RA --> UA[int_usable_arrivals<br/>high + medium only, ADR-016]
    RA --> HW[fct_headways<br/>valid / train_between /<br/>unproven_order / implausible]
    RA --> ARD[agg_arrival_reconstruction_daily]
    TU[(trip_updates)] --> SP[fct_stop_predictions]
    SP --> PE[fct_prediction_errors<br/>last snapshot per horizon bucket]
    UA --> PE
    AL[(alerts)] --> AA[agg_active_alerts]
    AA --> DLH[int_disrupted_line_hours]
    OW[int_observation_window<br/>+ missed_runs check] --> HF[int_hour_flags<br/>data_outage]
    PE & HF & DLH --> ACCD[agg_prediction_accuracy_daily]
    UA & HF & DLH --> DEVH[agg_actual_deviation_hourly]
    HW & HF & DLH --> HWH[agg_headway_adherence_hourly]
    UA & HF & DLH --> ETTD[agg_ett_daily]
    ARH[agg_line_reliability_hourly<br/>metrics_as_of] --> WH[int_window_hours<br/>last_24h, last_7d]
    WH --> ACCW[agg_accuracy_window]
    WH --> DEVW[agg_actual_deviation_window]
    WH --> HWW[agg_headway_window]
    WH --> ETTW[agg_ett_window]
    PE --> ACCW
    UA --> DEVW
    HW --> HWW
    UA --> ETTW
```

- **Reconstruction.** An arrival is the midpoint between the last observation before a vehicle's
  first `STOPPED_AT` at a stop and that observation (`observed`), or an interpolation along the
  shape between the observations either side (`bracketed`). Confidence is `high` for an interval
  up to 30 s, `medium` up to 120 s, otherwise `low`. A reused trip id starts a new `segment`.
  `int_usable_arrivals` keeps high and medium only and marks the segment nearest the schedule
  (`is_nearest_segment`); every Release 2 metric reads that model, never the low arrivals.
- **Outage and disruption flags.** `int_hour_flags.data_outage` marks hours with three or more
  missed runs or an incomplete vehicle window: metrics leave them out and count them
  (`n_excluded_outage`). `int_disrupted_line_hours` marks line hours with an active detour,
  suspension or shuttle alert: those hours stay in the statistics and are counted
  (`service_disruption`, `n_disruption`).
- **Windows.** `int_window_hours` lists the service hours of `last_24h` (24 hours) and `last_7d`
  (168 hours) ending at the latest hour of `agg_line_reliability_hourly` (the export's
  `metrics_as_of`). The four `agg_*_window` marts compute medians, p90 and shares over every
  eligible row of those hours, so the app never averages per-hour or per-day medians for a window.
  The daily and hourly marts remain for trends; the app labels any value built from them
  "approximate".
- **Gold.** The `gold_r2_allowlist` macro adds the six daily/hourly aggregates and the four window
  marts to the export when the flag is on; `publish.R2_GOLD_TABLES` publishes the same set to
  MotherDuck.
