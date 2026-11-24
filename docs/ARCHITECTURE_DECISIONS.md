# Architecture decisions

One numbered set for Release 1 and the decisions already taken for later releases. Numbers are
stable: other documents and code comments cite them as `ADR-NNN`.

| # | Decision | Status |
| --- | --- | --- |
| 001 | Lambda + EventBridge instead of Kafka | Accepted |
| 002 | Parquet on S3 instead of Iceberg | Accepted |
| 003 | MotherDuck instead of Snowflake | Accepted |
| 004 | Event time, feed time and ingestion time kept separate | Accepted |
| 005 | Duplicates and out-of-order events | Accepted |
| 006 | Data outage versus transit disruption | Accepted |
| 007 | Snapshot predictions are not arrivals | Accepted |
| 008 | Preventing unsupported AI claims | Proposed (R3) |
| 009 | Cached live map | Accepted |
| 010 | Streamlit first, then Next.js + FastAPI | Accepted (Streamlit); Proposed (Next.js + FastAPI) |
| 011 | FastAPI and MCP as separate interfaces | Proposed (R3/R4) |
| 012 | dbt in Lambda DuckDB, MotherDuck gold-only, DuckDB 1.5.6 pin | Accepted |
| 013 | Duplicate deliveries flagged, never dropped | Accepted |
| 014 | Vehicle positions sampled every 15 s | Accepted (R2) |
| 015 | Immutable history of daily aggregates, versioned per metric family | Accepted (R2) |
| 016 | Confidence levels: only high and medium arrivals feed metrics | Accepted (R2) |
| 017 | Chaos switch for failure drills | Accepted (R2) |
| 018 | Elementary on dbt-duckdb: anomaly tests, persisted history, daily report | Accepted (R2) |

Template: Status, Context, Decision, Consequences, Revisit when.

### ADR-001: Lambda + EventBridge instead of Kafka

- **Status:** Accepted
- **Context:** The MBTA publishes three realtime feeds as whole-file snapshots (protobuf over
  HTTPS) that change every few seconds. Reliability metrics need a capture about once a minute,
  not a record-by-record stream, and the project is run by one person on a small budget.
- **Decision:** An EventBridge Scheduler rule invokes the `headway-ingest-rt` Lambda every minute
  (`cron(* * * * ? *)`, aligned to the minute). Each run fetches the three feeds, writes the raw
  bytes to S3 and one manifest, and refreshes the live snapshot. Curation (every 15 minutes),
  compaction (hourly), the static GTFS load (daily) and transform (hourly) are further scheduled
  Lambdas. Retries are 0 everywhere; the next scheduled run replaces a missed one.
- **Consequences:** There is no broker to run and idle cost is close to zero. Delivery is
  at-least-once and at the scheduler's mercy, which is why duplicates and gaps are first-class
  data (ADR-005, ADR-013). Capture is one poll per minute for trip updates and alerts and, since ADR-014,
  four polls per minute for vehicle positions; a capture is in gold roughly 20 to 80 minutes after
  it is fetched.
- **Revisit when:** a source offers a push or streaming API, sub-minute capture is needed, or
  several cities make per-minute invocations the dominant cost.

### ADR-002: Parquet on S3 instead of Iceberg

- **Status:** Accepted
- **Context:** The data is append-mostly, partitioned by hour, and rebuilt from raw when logic
  changes. Writers are a few Lambdas that never overlap on the same partition.
- **Decision:** Curated tables are plain Parquet files in hive-style `dt=/hour=` partitions
  written by deterministic batches (the part file name is a hash of its `capture_id`s, so a
  retry overwrites itself). Compaction merges an hour only after a row-count check. Readers glob
  the prefix through DuckDB `read_parquet`. Raw objects and manifests are the source of truth and
  are never modified.
- **Consequences:** No catalog, no table-format service, trivially replayable and inspectable.
  There are no transactional multi-file commits, schema evolution is by convention and listing
  cost grows with object count (mitigated by curating manifests and quarantine as Parquet).
- **Revisit when:** concurrent writers or row-level updates are needed, schema evolution becomes
  frequent, or listing dominates run time.

### ADR-003: MotherDuck instead of Snowflake

- **Status:** Accepted
- **Context:** The warehouse only has to hold small finished gold tables for ad hoc SQL; the
  analytical engine already runs in DuckDB. A conventional warehouse would add idle cost and a
  second SQL dialect.
- **Decision:** Gold is published to MotherDuck (DuckDB in the cloud) as one table per gold
  table. The public app reads Parquet over HTTPS and does not depend on MotherDuck at all.
- **Consequences:** One SQL dialect from laptop to cloud, a free tier that fits the data size, and
  a warehouse that can be lost without losing anything (gold is rebuilt from S3). The engine
  version is dictated by MotherDuck support (ADR-012).
- **Revisit when:** the free tier no longer covers publishing, multi-user concurrency or access
  control is needed, or data outgrows a single-node engine.

### ADR-004: Event time, feed time and ingestion time kept separate

- **Status:** Accepted
- **Context:** A value has three clocks: when it happened or is predicted (event time), when the
  feed says it was produced (`feed_header_ts`), and when Headway fetched it (`fetched_at`, plus
  `scheduled_at` for the minute that triggered the run). Mixing them turns pipeline delay into
  apparent transit delay.
- **Decision:** All three are stored side by side from raw through gold, in UTC. Partitions use
  `fetched_at`. Metrics use event times (predicted and scheduled arrival) and the snapshot's
  `collected_at`; freshness and the observation window use fetch times. The app shows "metrics as
  of" (the data period) separately from "feed freshness" (age of the exports).
- **Consequences:** Every table carries extra timestamp columns and the metric definitions state
  which clock they use (see METRICS.md). Local hours and service dates (America/New_York, 03:00
  service-day cutoff) are derived at the edge, never stored.
- **Revisit when:** a source provides authoritative event timestamps that make one of the clocks
  redundant.

### ADR-005: Duplicates and out-of-order events

- **Status:** Accepted
- **Context:** Feeds repeat unchanged payloads, a minute can be delivered twice (ADR-013), and
  feed timestamps do not always increase between captures.
- **Decision:** Raw captures are never deduplicated. Each carries a `content_hash`; a capture
  whose hash equals the previous successful capture of the same feed is flagged
  `is_duplicate_capture` and excluded from prior snapshots, eligible predictions, coverage and
  capture minutes, but stays visible in pipeline health. Revisions are computed against the
  previous non-duplicate snapshot ordered by collection time, not by arrival order.
- **Consequences:** Replays are exact and no evidence is lost; models pay for a window function
  and a flag. A feed that republishes stale content is visible as duplicates instead of as
  stability.
- **Revisit when:** a feed offers a reliable sequence number or ETag that makes content hashing
  unnecessary.

### ADR-006: Data outage versus transit disruption

- **Status:** Accepted
- **Context:** A gap in a metric can mean the trains stopped or that Headway stopped looking.
  Reporting both as "bad service" would be wrong in the first case and unfair in the second.
- **Decision:** Aggregates only cover hours inside the observation window (at least one
  successful non-duplicate `trip_updates` capture; `window_complete` at 55 or more capture
  minutes). Coverage is the share of observable trip stops with an eligible prediction, shown on
  every card; below 50% values are withheld and lines are ranked only across comparable coverage.
  Missed runs, failed fetches and quarantine are reported on the Pipeline health page and in
  hourly quality checks. Alerts are carried as data, not interpreted as outages.
- **Consequences:** Some hours show no value instead of a misleading one. Users must read
  coverage next to every number.
- **Revisit when:** an authoritative disruption signal is joined in, which would allow labelling
  the cause of a gap rather than only its existence.

### ADR-007: Snapshot predictions are not arrivals

- **Status:** Accepted
- **Context:** The realtime feed publishes predicted arrival times, not observed ones. Treating
  the final prediction as the arrival would present a forecast as a measurement.
- **Decision:** Scheduled and predicted values live in separate columns. The Release 1 metrics
  use the last snapshot collected before the scheduled arrival, within a 30-minute horizon,
  schedule matched and from a non-duplicate capture; implausible matches (over an hour off) are
  left out and counted. Every metric is named "predicted ..." and the app states that these are
  Headway approximations, not official MBTA metrics.
- **Consequences:** The metrics measure what riders are told, not what trains did. Observed
  arrivals from vehicle positions are a possible later addition.
- **Revisit when:** vehicle positions are reliable enough to derive observed arrivals.

### ADR-008: Preventing unsupported AI claims

- **Status:** Proposed (Release 3)
- **Context:** A natural-language layer over the metrics can state things the data does not
  support: a number without coverage, a cause for a delay, or a comparison across unlike lines.
- **Decision (proposed):** The assistant may only answer from tool results that carry coverage
  and freshness, must quote them, must refuse causal claims, and every answer is checked against
  the tool output before it is shown. The rules are tested with a fixed question set.
- **Consequences:** Answers are narrower and sometimes decline to answer, in return for being
  traceable to a gold row.
- **Revisit when:** the design is implemented in R3, or evaluation shows the checks block
  legitimate answers.

### ADR-009: Cached live map

- **Status:** Accepted
- **Context:** The live map needs current vehicle positions, but visitors must not trigger
  fetches against the MBTA and the public bucket must stay cheap.
- **Decision:** The ingest Lambda writes `public/<city>/live/vehicles_latest.json` once per run
  (subway vehicles only, invalid rows dropped and counted, stale positions flagged) with
  `Cache-Control: max-age=30`. The app reads that object through the public HTTPS URL, caches it
  for 60 seconds, and warns when the snapshot is old.
- **Consequences:** The map is at most about a minute and a half behind the feed, load on the
  source is independent of traffic, and the app holds no feed URLs or credentials.
- **Revisit when:** a sub-30-second map is required, which would need a streaming path.

### ADR-010: Streamlit first, then Next.js + FastAPI

- **Status:** Accepted for Streamlit (R1); Next.js + FastAPI Proposed (R4)
- **Context:** Release 1 needs a public, read-only app quickly; a custom front end and API are
  only worth their cost once the metrics are stable.
- **Decision:** The Release 1 app is Streamlit on Community Cloud, reading public Parquet and JSON
  over HTTPS with no credentials. Metric logic lives in the dbt models, so the UI holds none. A
  Next.js front end on a FastAPI service replaces it when the product needs it.
- **Consequences:** Fast to build and host for free; limited layout control and per-session
  reruns. Replacing the UI later does not touch the pipeline because gold is the contract.
- **Revisit when:** page latency, layout or authentication needs exceed what Streamlit offers.

### ADR-011: FastAPI and MCP as separate interfaces

- **Status:** Proposed (R3/R4)
- **Context:** Humans and AI clients need different contracts: an HTTP API for the web front end
  and tools for assistants.
- **Decision (proposed):** A FastAPI service serves the web front end; an MCP server exposes
  read-only metric tools to assistants. Both are thin layers over the same gold tables, share a
  metrics access module, and are deployed and versioned separately.
- **Consequences:** Two surfaces to maintain, but neither is shaped by the other's constraints
  and the assistant's tools can enforce ADR-008 without affecting the web API.
- **Revisit when:** one surface proves redundant.

### ADR-012: dbt compute in Lambda DuckDB, MotherDuck gold-only, DuckDB 1.5.6 pin

- **Status:** Accepted
- **Context:** The gold tables must refresh without anyone running dbt by hand, and the data stays
  small (a few hundred MB of realtime Parquet per week). MotherDuck's free tier has a limited
  monthly compute budget; running every hourly dbt build there would spend it on transformation
  rather than on serving.
- **Decision:** The transform Lambda runs `dbt build --target lambda` against an ephemeral DuckDB
  file in `/tmp`, reading curated Parquet, manifests and quarantine straight from S3 through
  httpfs with the function role. Realtime sources are bounded by a rolling `lookback_days` window
  (default 7, applied on the `dt` partition in the staging models only); static GTFS tables keep
  the feed-version logic. After the build it exports the gold allowlist to the public bucket and
  then replaces one MotherDuck table per gold table (`CREATE OR REPLACE TABLE ... AS SELECT *
  FROM read_parquet(...)`). MotherDuck therefore only stores and serves finished tables; its
  compute is spent on the publish, not on dbt. dbt test failures are counted and logged but do not
  block the export. Once a day the same function writes `gold/cost_daily.parquet` from Cost
  Explorer (project tag) joined to capture counts.
- **Consequences:** Every run rebuilds from the last 7 days, so gold holds a rolling window rather
  than all history and nothing is carried between invocations. S3 listing cost grows with the
  number of curated and manifest objects, because the globs are listed before the `dt` filter
  prunes files. The dbt environment needs its own container image (dbt pins protobuf<7), with
  the DuckDB extensions installed at build time.
- **DuckDB version constraint:** The DuckDB version is constrained by MotherDuck support, not by
  what is newest. The motherduck extension refuses unsupported engines (DuckDB 2.0.1 failed with
  "not yet supported ... We recommend DuckDB v1.5.6"), so `duckdb==1.5.6` is pinned in `dbt/` (the
  transform image) and in the root dev environment. The image build asserts the pinned version and
  bakes the httpfs, aws and motherduck extensions for it (`v1.5.6/linux_arm64`). Bump the pin only
  after MotherDuck lists the new version as supported.
- **Revisit when:** history beyond the lookback is needed (R2 adds incremental history, keeping
  older partitions rather than rebuilding them), the build approaches the 900 s timeout (the Lambda maximum), or
  MotherDuck limits change.
- **Reading S3 once per run:** Realtime staging and all intermediate models are tables, so the
  curated files are scanned once per run instead of by every downstream model and test (views over
  S3 made a trivial test cost 50-110 s with a day of data). Run manifests and quarantine records
  are also curated as hourly Parquet (`table=manifests`, `table=quarantine`) because the raw
  objects are one JSON file per ingest run / capture, so the file count grows with every minute of
  ingest; the raw JSON stays the source of truth and the `backfill_manifests` curate task rebuilds
  the Parquet from it. Overlapping transform runs are prevented with an S3 lock object
  (`state/<city>/transform.lock`, conditional put; a lock older than 15 minutes is stale).

### ADR-013: Duplicate deliveries are flagged, never dropped

- **Status:** Accepted
- **Context:** EventBridge Scheduler delivers at least once, so a minute can reach the ingest
  Lambda twice, even within the same second. Both runs fetched and wrote a manifest, which put the
  same `capture_id` in two runs and broke the grain tests (see INCIDENTS.md).
- **Decision:** Retries stay at 0 on every schedule and function. The manifest key is derived from
  the scheduled minute and written with a conditional create (`IfNoneMatch='*'`). The delivery
  that loses writes a small duplicate-delivery record under `manifests/<city>/duplicates/`, logs it
  and exits normally. Duplicates are flagged, never silently dropped: curation records them in the
  manifests table with status `duplicate_delivery` (their captures are not curated as data),
  `agg_pipeline_health.duplicate_delivery_count` and the hourly `duplicate_deliveries` check
  count them, and `stg_manifests` deduplicates on `capture_id` (earliest `fetched_at`, then lowest
  `run_id`) so history written before this change satisfies the grain tests.
- **Consequences:** The second delivery still fetches the feeds and may leave raw objects with
  other capture ids that no manifest references; they are not curated. Gold objects are served with
  `Cache-Control: max-age=300` and the live snapshot with `max-age=30`.
- **Revisit when:** a duplicate-heavy period makes the wasted fetch matter (claim the minute before
  fetching), or the scheduler offers exactly-once delivery.

### ADR-014: Vehicle positions sampled every 15 s

- **Status:** Accepted (built; not deployed yet)
- **Context:** Release 2 reconstructs arrivals from vehicle positions captured once a minute. The
  spike in `docs/spikes/2026-11-sampling.md` (about 27 hours, scheduled subway trips) found: median
  observed interval 51-59 s; 13 % of trip-stops high confidence; hold-out median error 41-57 s on
  seven of eight lines (Mattapan 33 s), p90 70-100 s, only 16-30 % within 30 s. The vehicle feed
  header changes every 5 s and the median vehicle updates every 9 s, so most updates are never
  captured.
- **Decision:** `trip_updates` and `alerts` stay at one capture per minute. `vehicle_positions` is
  sampled every 15 s: four polls per minute invocation, because EventBridge Scheduler cannot
  schedule below one minute. Poll 0, at the scheduled time, fetches all three feeds as before;
  polls 1-3 fetch vehicle positions only, at the scheduled time plus 15, 30 and 45 s. A poll that
  starts more than 5 s after its slot is skipped, and nothing starts after 55 s, so a run that
  starts late loses polls rather than overrunning. Each poll is its own capture with its own
  capture id; manifest entries carry `poll_index` 0-3 and all share the minute's `scheduled_at`, so
  a minute is still one manifest (claimed with the conditional create of ADR-013) and still counts
  as one capture minute in the observation window. The live snapshot is rewritten after every
  vehicle poll. The ingest Lambda is sized at 256 MB with a 58 s timeout. It was first deployed at 128 MB
  and Lambda reported 114 MB used (89 %), so it went to 256 MB (see INCIDENTS.md, "Ingest Lambda
  memory underestimated"): 127 to 128 MB used (50 %), single-poll runs 2.5 s. Budget alerts move from USD 5 / 10 to
  USD 8 / 15.
- **Cost:** per 30-day month, us-east-1 list prices, to be checked against the bill after deploy.
  - Lambda, the dominant item: the last poll is due 45 s after the scheduled time, so a run that
    starts on the minute lasts about 48 s (if the 17 s start delay measured in MEASUREMENTS.md is
    a delay before the invocation starts, it is about 30 s and the cost is a third lower). At
    48 s and 0.25 GB that is about 12 GB-s per invocation and 518,400 GB-s a month. At USD 0.0000133334 per GB-s (arm64) that is about USD 6.91 before the
    free tier (400,000 GB-s a month for the whole account, which the other functions also use).
    Before, a run of an estimated 3-5 s at 256 MB was about 32,000-54,000 GB-s, USD 0.43-0.72
    (the old duration was not measured). At the former 128 MB size the new runs would have cost
    about USD 3.5.
  - S3 PUTs at USD 0.005 per 1,000: raw vehicle captures 43,200 to 172,800 (+129,600), live
    snapshot PUTs to the public bucket 43,200 to 172,800 (+129,600); +259,200 PUTs, about
    USD 1.30. Curate reads 3 more small objects per minute, under USD 0.10.
  - Storage and rows: about 130,000 more raw objects a month (vehicle bodies are small, 49 KB
    average before gzip), and curated vehicle rows grow from about 98,000 to about 392,000 a day.
  - Total: about USD 6-8 more a month at 256 MB, which is why the budget alerts move.
- **Consequences:** One invocation now lasts most of a minute, so a delayed or duplicated
  scheduler delivery overlaps the next minute (ADR-013: a duplicate delivery still runs all four
  polls before it finds the minute claimed; claiming earlier is the open item there). Curated
  `vehicle_positions` has up to four captures a minute with the same position repeated until the
  vehicle reports again; arrival models de-duplicate on vehicle and `event_ts`. The local smoke
  (`make ingest-smoke`) measured 60 to 70 MB resident, but that leaves out the Lambda runtime's
  overhead; production showed 114 MB at 128 MB, hence 256 MB. The smoke now fails above 50 % of
  the configured memory. The confidence thresholds (30 s high, 120 s medium) stay as in the spike method.
  The quarter-minute accuracy is expected from the interval width, not yet measured: see
  `docs/MEASUREMENTS.md` after the first days of 15 s data.
- **Revisit when:** two full days of 15 s data allow the spike to be repeated, real Lambda
  duration or memory differs from the above, or the cost per month exceeds the USD 8 alert.

### ADR-015: Immutable history of daily aggregates, versioned per metric family

- **Status:** Accepted (R2; active only when `HEADWAY_ENABLE_R2=true`)
- **Context:** The transform rebuilds every table from the last 7 days of curated data each hour,
  so gold forgets everything older than a week, and a late fix to the raw or curated data would
  silently change numbers that were already shown. SLO rollups need 30 days, and accuracy trends
  need more.
- **Decision:** A service day closes at 06:00 local on the day after its service date (the day
  ends at 03:00; three hours let curation catch up). After that, the transform writes the day of
  each daily aggregate once, as Parquet, to
  `s3://<public>/public/<city>/history/table=<agg>/metric_version=<N>/service_date=YYYY-MM-DD/part.parquet`.
  An object is created with a conditional put (`If-None-Match: *`); an existing day is never
  rewritten, and a day cut by the edge of the lookback window is never written. `metric_version`
  comes from one constant per metric family (`METRIC_VERSIONS` in `headway_transform/history.py`):
  a change to the definition of any aggregate of a family bumps its version, which writes next to
  the old data instead of over it. dbt reads closed days from history and open days from the
  7-day build (`history_union`), so gold spans more than a week while open days stay live. An
  explicit backfill is the transform task `{"task":"rebuild_history","from","to","metric_version"}`
  (at most 14 days; the version must be the family's current one, because the code that builds the
  rows is the current code). History tables are also published to MotherDuck as
  `history_<agg>`, with every version and `metric_version` as a column.
- **Consequences:** A day written while the pipeline had only partly captured it (R2 switched on
  mid-day, an outage) stays as written; its coverage columns say so. Correcting a past day means
  a new version, not an edit. The transform role may create objects under `history/` but not
  delete them. Storage is small (one file per table and day).
- **Revisit when:** a correction without a definition change is needed often (add a
  `revision` partition), or history outgrows reading it with DuckDB globs.

### ADR-016: Confidence levels: only high and medium arrivals feed metrics

- **Status:** Accepted (R2)
- **Context:** Arrivals are reconstructed from vehicle positions sampled every 15 s (ADR-014), so
  the arrival time of a stop is only known to within an interval. The 2.0 spike found that the
  error is small when the interval is short and grows quickly when it is not.
- **Decision:** Each reconstructed arrival has a confidence. `high`: observed (a `STOPPED_AT`
  after a previous capture) with an interval of 30 s or less. `medium`: observed with an interval
  up to 120 s, or bracketed between two observations no more than 120 s apart. `low`: anything
  else, and every arrival of a run that breaks the monotonic order. Missing arrivals have no
  confidence. Only `high` and `medium` arrivals feed a metric value (`int_usable_arrivals`);
  `low` and missing are counted in `agg_arrival_reconstruction_daily` and left out. The one use
  of low arrivals is as evidence: a train of any confidence between two others at a stop makes
  that pair an invalid headway. Coverage (usable over scheduled or reconstructed trip stops) is
  shown beside every value, and a value below 50 % coverage is withheld.
- **Consequences:** Coverage, not accuracy, absorbs the missing 15 s samples: busy lines with
  many updates are well covered, slow ones less so. The thresholds are shared by every metric, so
  changing them changes every Release 2 aggregate and is a metric-version bump (ADR-015).
- **Revisit when:** the first days of 15 s data show that the high/medium share or the hold-out
  error differ from the spike (see `docs/MEASUREMENTS.md`).

### ADR-017: Chaos switch for failure drills

- **Status:** Accepted (R2)
- **Context:** The alarms, the data-outage labelling and the recovery paths are only believed once
  they have been seen to work. Waiting for the MBTA or MotherDuck to fail on its own is slow, and
  editing code or IAM to fake a failure leaves a deployed pipeline different from the tested one.
- **Decision:** One SSM parameter, `/headway/chaos`, holds `{"mode": "feed_down" | "md_down" |
  "off", "until": "<UTC ISO>"}`. It is created by hand for a drill (Terraform never creates it,
  and the roles can read it but not write it), so a fresh deployment cannot be in a drill. A mode
  is active only before `until`; a missing parameter, malformed JSON, an unknown mode, a missing
  `until` or any error reading it (read once per invocation, 2 s timeout, one attempt) means
  `off`. While active, each invocation logs `CHAOS ACTIVE mode=... until=...`. `feed_down`: ingest
  fetches nothing and records every feed as `status = error` (the minute is still claimed and its
  manifest written), as a real outage of the feeds would. `md_down`: the transform's MotherDuck
  publish raises before it reads the token; gold is exported to S3 first, exactly as when
  MotherDuck is really down. Ingest and transform carry their own copy of the 70 lines that read
  it (they ship in different artifacts); a test keeps the two in step. The drills and how to start
  and stop them are in `docs/RUNBOOK.md`.
- **Consequences:** A drill is a real, recorded outage: the hour is labelled `data_outage`, the
  SLO budgets are spent, and the alarms fire (the 15-minute window is chosen to trip the ingest
  alarms). Drills are logged in INCIDENTS.md and marked as drills. The permission to read one
  parameter is the only addition to the roles.
- **Revisit when:** a drill needs a mode that cannot be made by failing a fetch or the publish
  (for example a corrupt payload), or a second environment needs its own switch.

### ADR-018: Elementary on dbt-duckdb: anomaly tests, persisted history, daily report

- **Status:** Accepted (R2)
- **Context:** The dbt tests say whether a run is correct, not whether the data looks like
  yesterday's. Row counts and freshness of the large tables can drift without any test failing.
  Elementary adds anomaly tests and an HTML report, but it must work on DuckDB, inside the
  transform image (arm64, Python 3.12, read-only root, `/tmp` only, no network at runtime), and
  its results must outlive a container whose database is rebuilt every hour.
- **Verified before adopting** (package metadata and sources, then the image smoke test):
  the Elementary dbt package 0.26.1 requires dbt `>=1.0,<3.0` (we run 1.11) and ships `duckdb__*`
  macros; the `edr` CLI 0.26.1 has a `duckdb` extra that requires `dbt-duckdb>=1.8,<2` (we run
  1.10), and the whole set resolves with our pins (dbt-core, dbt-duckdb, duckdb 1.5.6, protobuf
  unchanged; networkx goes from 3.7 to 2.8.8 because `edr` pins `<3` and dbt-core allows it). Two
  of its dependencies, `grapheme` and `ratelimit`, publish no wheel and are built from source
  (pure Python) in the image's build stage. Under the smoke constraints `edr report` runs
  in-process (dbt is a library there, no `dbt` binary is needed), renders about 8 MB of HTML from
  the DuckDB file and needs three accommodations, all in `observability.py`: its internal dbt
  project and ours have their packages installed at build time (`dbt deps` would otherwise download
  at runtime and write to the image), its default target directory is created at import, so it is
  imported from a `/tmp` working directory, and telemetry is switched off in a generated config.
- **Decision:** `elementary-data[duckdb]` and the dbt package are pinned to the same release
  (`dbt/pyproject.toml`, `dbt/headway/packages.yml` with its lock file). `elementary.volume_anomalies`
  and `elementary.freshness_anomalies` run, as warnings that never block an export, on
  `fct_stop_predictions` (`collected_at`), `fct_reconstructed_arrivals` (`arrival_ts`, Release 2 only)
  and `agg_line_reliability_hourly` (`service_date`: the table has no timestamp, so freshness is the
  age of the newest service day). Elementary writes its tables into the run's DuckDB file. The two
  that carry history, anomaly test results and the data-monitoring metrics (the baseline of every
  anomaly test), are persisted with the run-state mechanism of `state.py`: restored into the tables
  dbt has just built, before the tests run, and the rows the run created are appended at its end.
  The plain dbt test rows (about 600 a run) and the artifact tables are rebuilt each run and not
  persisted. On the first run at or after 06:00 UTC the report is generated and uploaded to
  `public/boston/observability/index.html`; the Pipeline health page links to it. Any failure of
  the restore, the persist or the report is logged and never fails the run.
- **Consequences:** The image grows (about 1.2 GB with the extra packages and the report assets)
  and every run builds about 30 Elementary models and records their results (a few seconds). The
  anomaly tests need a training window of days before they say anything; until then they pass.
  `make check` and the scripts that build dbt install the packages once (`scripts/dbt_deps.py`).
  The report is public like the rest of `public/`; sampling of data rows is switched off in it.
- **Revisit when:** Elementary stops supporting dbt-duckdb or the pinned dbt, the report outgrows a
  daily rebuild, or anomaly warnings are trusted enough to alarm. The alternative if it breaks:
  the dbt test results and run history are already persisted (`agg_transform_runs`); a small HTML
  test-history page rendered from that table would replace the report.
