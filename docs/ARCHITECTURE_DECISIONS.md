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
| 008 | Preventing unsupported AI claims | Accepted (R3, incident summaries); Proposed (assistant) |
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
| 019 | Forecasting design: target, horizons, leakage rule, gate, baselines | Accepted (R3) |
| 020 | Analyst data access through the public gold tables | Accepted (R3, Phase 3.5) |
| 021 | LLM cost and abuse controls | Accepted (R3, Phase 3.5) |
| 022 | Weather source for the weather comparison | Accepted |
| 023 | Public read-only metric API | Accepted (R4) |
| 024 | Account Lambda memory cap | Accepted |

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

- **Status:** Accepted for the incident summaries (R3, Phase 3.4) and for the AI analyst (R3,
  Phase 3.5, ADR-020, ADR-021)
- **Context:** A language model that writes about the metrics can state things the data does not
  support: a number that is not there, a cause nobody measured, a comparison across unlike lines.
  Incident summaries are the first place Headway lets a model write text a rider reads.
- **Decision:**
  - The model sees only the evidence JSON of one incident (`fct_incidents.evidence_json`: the hours
    with their values, baselines, thresholds, coverage, alert ids and headers) and a fixed
    instruction: describe what changed, where and when, using only numbers in the evidence, no
    causes unless quoting an alert header, say when coverage is low. It has no tools, no history
    and no other table.
  - **Blocking check** (`headway_transform/incident_numcheck.py`) on every text before it is stored:
    every number (integers, decimals, percentages, clock times, ISO dates, durations such as
    `3 min` or `2 hours`, ordinals, spelled-out numbers from two upward) must equal an evidence
    value within display rounding or after a listed unit conversion (seconds to minutes and hours,
    hours to minutes); a unit is not interchangeable (35 % is not 35 s). Causal wording (because,
    due to, caused by, as a result of, owing to, on account of) is allowed only inside a double-quoted
    segment that is a verbatim part of an alert header in the evidence. Any failure rejects the text.
  - A rejected text, a provider error, a missing key or an exhausted daily cap all end in the same
    place: the deterministic template (`template_summary`), written from the evidence alone and
    itself checked in tests. The page says "template used"; the rejection is counted
    (`incident_llm_rejections` in the run summary, `number_check` and `rejection_reason` on the row).
  - Generation is idempotent per incident version (`evidence_hash`), persisted in the raw bucket, and
    capped per day, so the cost has a ceiling and a re-run changes nothing.
  - Low coverage is not an LLM judgement: the page withholds the summary when any hour of the
    incident has under 50 % arrival coverage, as it withholds every other value.
  - The check cannot prove a sentence true. It proves that every number in it is one the evidence
    holds and that nothing is said about causes; wording such as "worsened" or "recovered" is not
    verified against the sequence of hours.
- **Consequences:** Summaries are plainer than a free-form model would write and sometimes fall
  back to the template, in return for being traceable to the evidence shown under each one. The
  provider sits behind an interface (`LlmClient`), so tests and the image smoke test run with fakes
  and no network.
- **Phase 3.5 additions:** the same check applies to analyst answers, against the tool results
  instead of an evidence JSON, and gains a **direction rule**: a direction word (improved, better,
  worsened, worse, increased, rose, decreased, fell, dropped, higher, lower, faster, slower) must agree
  with the sign of a related change in the evidence (`deviation_over_baseline_s`, or a `*_change` or
  `delta_*` field such as the value minus the previous one); where the evidence has no change,
  direction words are rejected. Whether higher is worse is read from the field name (on-time,
  within-band and coverage: higher is better).
- **Change log:**
  - Sign fix (bug fix, no change of policy). The check read figures without their sign, so it
    rejected every negative evidence value (a line running 86 s early could not be described). A
    signed figure (`-86 s`, `−86 s`, `minus 86 s`) now has to equal the signed evidence value
    within rounding. An unsigned figure is read as positive, and matches a negative value only when
    its clause carries a sign word. The direction rule gained those words: early, ahead, faster,
    under schedule need a negative related value (seconds or a change) in the evidence; late,
    behind, slower, over schedule a positive one. `86 s early` is accepted for -86, `86 s late` and
    `-86 s` for +86 are rejected. Found by the analyst evals (`docs/EVALS.md`).
  - Weather comparison (Phase 3.7, ADR-022). The comparison is descriptive: "on snow days, on-time
    share was X% vs Y%" with the day counts and an interval. Causal wording is never used and the
    figures are withheld under 5 days of either kind. The classifier pilot (C12) is separate and
    nothing in production depends on it.
- **Revisit when:** the assistant (R4) is designed, which needs the same rules over tool results,
  or the rejection count shows the check blocking legitimate text (read `rejection_reason` in
  `incident_summaries`).

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

- **Status:** Accepted for Streamlit (R1); the FastAPI half is built in R4 Phase 4.1 (ADR-023);
  the Next.js front end stays Proposed
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
  USD 8 / 15 (the warning was later raised to USD 12; the USD 15 alerts are unchanged).
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
  duration or memory differs from the above, or the cost per month exceeds the USD 12 alert.

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

### ADR-019: Forecasting design: target, horizons, leakage rule, gate, baselines

- **Status:** Accepted for the parts built: the examples, leakage rule, gate, baselines, LightGBM
  model, registry and serving (R3, Phases 3.1 to 3.3) and the paired-bootstrap comparison rule. The
  published model-versus-baseline comparison waits for the training gate: no line has passed it
  yet, so nothing is trained and no comparison is published.
- **Context:** Release 3 forecasts arrival delay. The only observed delay Headway has is the
  reconstructed arrival (ADR-016), known to within 15 to 120 s, and only after the captures that
  determine it exist. A forecast that is evaluated against data it could not have had at the time
  looks better than it is, so the design fixes what may be used before any model is chosen.
- **Decision:** forecast the delay of the next 15, 30 and 60 minutes with a single LightGBM model,
  built only from features known at prediction time (the leakage rule), trained only on lines past
  the gate, registered under a versioned `models/forecast/` registry and served by the hourly
  transform. It is compared with three baselines by a paired cluster bootstrap and reported only
  when it beats the best of them. The parts below give each element.
- **Target:** the delay (reconstructed arrival minus scheduled arrival, seconds) of a trip stop
  scheduled to arrive in the 15-minute slot starting at `t + horizon`, for horizons of 15, 30 and
  60 minutes, from high and medium confidence arrivals only. Prediction times `t` fall every 15
  minutes in the service hours (05:00 to 01:00 local). Examples in data-outage hours are dropped,
  for the hour of `t` and the hour of the target (`fct_forecast_examples`, internal, never exported).
  Only trip stops that were reconstructed become examples: a trip stop never observed has no label,
  so the examples describe the observed part of the service and the label coverage is reported
  with every result.
- **Leakage rule:** every feature uses only what was known at `t`. An arrival is known when the
  capture that determines it exists (`known_at` on `fct_reconstructed_arrivals`: the capture of the
  first STOPPED_AT for an observed stop, the later anchor for a bracketed one, and the neighbouring
  reconstructed stops that its confidence rests on), a headway when both arrivals and the arrivals that prove the order at the neighbouring stops are, a snapshot
  at `collected_at`, an alerts capture at `fetched_at`. Features: line, direction, stop position,
  hour, day type, scheduled headway, the last known delay of the same run upstream, rolling median
  delay of the line over 30 and 60 minutes, headway adherence, active line-scope disruption,
  skipped share of the last hour and MBTA's own predicted deviation. The outage filter selects rows
  by looking at whole hours; it is a choice of training rows, not a feature. Two tests hold the
  rule: a unit test per feature with rows from after `t` (and one known only after `t`) that must
  not count, and a truncation check (`scripts/leakage_check.py`, run by `make e2e`) that cuts every
  input at `t`, rebuilds and requires the same features.
- **Gate:** a line is trained on only when it has at least 21 service days with arrival coverage of
  at least 70 % (the arrival coverage of the SLO), at least 3 of them weekend days
  (`agg_forecast_gate`). Below the gate nothing is trained and nothing is reported as a forecast.
  The 27 hours of the sampling spike pass for no line.
- **Baselines (implemented in `ml/`, Phase 3.1; `make backtest`):** (1) persistence: the last known delay of the
  same trip (`last_upstream_delay_s`); (2) the average delay of the same day type and hour over the
  training weeks; (3) the rolling 60-minute median delay of the line. A model is reported only
  against all three, per line and horizon, on days after the training days. MBTA's predicted
  deviation is a labelled reference line in every report, not a baseline: it is the thing a rider
  can already read, and it is a feature of the models.
- **Honest reporting:** errors are measured against reconstructed arrivals, so they include the
  reconstruction error (15 to 120 s) and cannot be better than it; the report says so. Coverage
  (examples over scheduled trip stops of the slot) is shown with every number, a value from a slice
  with under 50 % coverage is withheld (ADR-016), and a line below the gate has no forecast.
- **Model (Phase 3.2, `ml/headway_ml/model.py`):** one LightGBM regressor of the delay in seconds
  over all lines past the gate, `line` a categorical feature, trained on the same features as the
  baselines (no extra input). The hyperparameters (leaves, minimum leaf size; 200 trees, seed 7,
  deterministic) are chosen from a small grid on the last 20 % of the service days of the
  training frame only, then the model is refitted on all of it, so a test week never takes part
  in tuning. Metrics stay per line x horizon, next to the three baselines and the MBTA reference.
  A line that has not passed the gate is not in the training rows; it gets the baselines and the
  reason.
- **Gate in code:** `train.run_training` trains only on lines with `gate_passed`, and
  `registry.write_model` refuses a model whose lines are not all passed, so no caller can write
  one around the gate. Without a passed line the run logs the baselines and the reason
  (`no line passed the training gate`) and writes nothing under `models/`.
- **Registry:** `s3://<raw>/models/forecast/v<N>/` holds `model.txt` (LightGBM booster),
  `lines.json`, `features.json`, `window.json`, `metrics.json` and `meta.json` (git sha,
  parameters, gate state). `models/forecast/latest.json` points at the newest version and is
  written last, only for a model trained under a passed gate; a failed run leaves it where it was.
  Rolling back is writing `latest.json` again for an earlier version.
- **Training data and job:** the transform (with `enable_r3`) writes each closed service day of
  `fct_forecast_examples` and `int_forecast_gate_days` once to `s3://<raw>/state/forecast_*`
  (immutable, conditional put, like the history), because a seven-day dbt lookback can never show
  21 days and a rebuild of weeks of examples would not fit the transform's 900 s. The weekly
  training Lambda (`headway-train`, container, arm64, Monday 06:00 UTC, no retry, behind
  `enable_r3`) reads those partitions (the last 56 days), runs the backtest and the training, and logs to
  MLflow. Only the rows a model is fitted on are capped (8000 per service day, stratified by line x
  horizon with the leftover of small lines given to large ones, seeded; per-line row counts are
  logged to MLflow). Every test week is scored in full, so the model, the baselines and the MBTA
  reference are scored on the same examples. MLflow uses a
  SQLite backend store: the database is downloaded from `s3://<raw>/mlflow/mlflow.db` at the
  start, used in `/tmp`, uploaded at the end, under a conditional-put lock (one weekly writer);
  artifacts go to `s3://<raw>/mlflow/artifacts/`.
- **Serving (Phase 3.3):** with `enable_r3` the hourly transform scores the next hour. The feature
  SQL is one macro (`forecast_features.sql`) used by the training examples and the serving models,
  and a parity check in `make e2e` requires identical features at the same `t`. The transform
  loads `models/forecast/latest.json` and its model once per cold start (cached in `/tmp`; the
  image carries LightGBM and only the serving modules of `headway_ml`, so the feature encoding is
  the training's own) and scores trip stops of the slots `[t + h, t + h + 15)` for h in 15, 30, 60.
  A line gets a model forecast only if it is in the approved model and past the gate now; any
  other line, a missing `latest.json` or an unreadable model gives baselines and the reason. The
  step never fails a run. `agg_forecast_next_hour` and `agg_forecast_gate_status` are exported
  with the other gold tables; the backtest summary from the training job
  (`models/forecast/reports/`) is exported as `agg_forecast_backtest` only for lines past the gate
  and only when one has passed, so a deployment whose gate never passed publishes no comparison.
- **Comparison rule:** the model is compared with the best of the three baselines on the strict
  view (every method has a value), by the paired cluster bootstrap of the MAE difference
  (prediction times resampled together for both methods). The page says "better" or "worse" only
  when the 95 % interval excludes 0 and "no significant difference" otherwise; the MBTA prediction
  is a labelled reference, never the baseline. Coverage under 50 % withholds a value.
- **Consequences:** The examples table is large (about 100 thousand rows per 27 hours for the whole
  network), so it is built only with `enable_r3`, which needs `enable_r2`, and is not published.
  `known_at` delays features by up to a few minutes compared with the arrival time, which is the
  honest cost of reconstructing arrivals from sampled positions.
- **Revisit when:** the gate is passed for a line, when the baselines show that the horizons or
  the slot width are wrong, or when the label coverage differs much between lines.

### ADR-020: Analyst data access through the public gold tables

- **Status:** Accepted (R3, Phase 3.5); refines ADR-011 (the MCP half; the FastAPI half stays proposed)
- **Context:** An AI analyst answers free-form questions. Whatever it can reach, a wrong or
  manipulated question can reach, so the reachable surface must be the smallest one that answers
  the questions, and the one the public already sees.
- **Decision:** `headway_mcp` exposes five read-only tools (`get_line_reliability`, `compare_lines`,
  `get_delay_trends`, `get_service_alerts`, `get_pipeline_health`) through FastMCP. They query only
  the public gold and history Parquet over HTTPS with DuckDB (`HEADWAY_DATA_URL`; `file://` in
  tests). No raw or curated bucket, no warehouse, no credentials, no free SQL: each tool builds one
  fixed SELECT from validated enums (lines, metrics of `docs/METRICS.md`, windows) and ISO dates.
  Limits: a date range of at most 31 days (alerts and health: 7), 500 rows, a 5 s query timeout.
  A value from a row under 50 % arrival coverage is returned as null, as on every page. A metric
  the export lacks gives a structured `not_available` with the list of available metrics. Every
  result carries provenance: tables, METRICS.md anchor, comparison period, coverage, freshness
  (`exported_at`) and the exact SQL that ran. One audit log line per call records the question id,
  tool, arguments, rows and latency. The Streamlit page runs client and server in process.
- **Consequences:** The analyst can say nothing the public cannot already read, and a leak is
  impossible by construction. It is limited to what gold holds (two windows, daily and hourly
  grain); history is read only for daily and hourly aggregates that have it.
- **Revisit when:** a question class needs data gold does not export, or the app moves to a
  separate API (ADR-010), when the same tools can be served remotely.

### ADR-021: LLM cost and abuse controls

- **Status:** Accepted (R3, Phase 3.5)
- **Context:** The analyst page is public and every live question costs a model call.
- **Decision:** A daily budget of questions (default 100, `HEADWAY_ANALYST_DAILY_BUDGET`) and a
  per-session limit (default 10, `HEADWAY_ANALYST_SESSION_LIMIT`). The daily count is kept in
  process and in a small JSON counter (date keyed, in the temp directory) so a restart does not
  reset it; a refused question is never counted. The model call has at most four tool rounds, a
  500-token answer, a 500-character question and temperature 0. Over budget, over the session
  limit or without the key (Streamlit secret `OPENAI_API_KEY`), the page shows 8 to 10 curated
  example questions whose answers were computed from the tools by the transform when R3 is on
  (`gold/analyst_examples.json`), with no model at view time. A question that tries to change the
  rules is refused before any model call, and only the five tool names are ever dispatched.
- **Consequences:** The worst-case daily cost is the budget times one bounded call. The counter is
  per app instance and best effort on a read-only or ephemeral disk; a second instance has its own.
- **Revisit when:** the page runs on more than one instance (move the counter to shared storage)
  or the budget is hit on most days.

### ADR-022: Weather source for the weather comparison

- **Status:** Accepted (R3, Phase 3.7)
- **Context:** The weather comparison (`agg_weather_comparison`, `docs/METRICS.md`) needs one
  daily value of snowfall, precipitation and maximum temperature for Boston, from a free source
  without a key. Checked on 2026-11-24 with plain `curl`:
  - **NWS `api.weather.gov` observations, station KBOS.** No key; a User-Agent is required.
    Public-domain US government data. It returns about 118 observations a day, near real time,
    but precipitation is sparse (`precipitationLastHour` was absent and
    `precipitationLast3Hours` null in the sampled day) and there is no daily snowfall, so a day
    would have to be assembled from partial hourly values.
  - **NOAA NCEI Access Data Service, dataset `daily-summaries` (GHCN-Daily), station USW00014739
    (Boston Logan, the KBOS airport station).** No key and no token; public-domain data; JSON,
    metric units on request. Daily `PRCP` (mm), `SNOW` (mm) and `TMAX` (C) as NOAA's own daily
    summary, for the same airport. Values trail real time by about a week (the latest value on
    the check date was 2026-11-15) and recent days can be corrected.
- **Decision:** Use NCEI `daily-summaries` for station USW00014739. The static Lambda
  (daily, only with `enable_r3`) refetches the last 21 days and rewrites one Parquet part per
  date in `curated/<prefix>/table=weather_daily/` (idempotent), so a late or corrected value
  replaces the old one. The station id is in the city config (`weather_station`). A failed fetch
  is logged and reported in the run summary; it never fails the GTFS load. The request carries
  no personal information. Day classes, with metric thresholds: snow day, snowfall of at least
  25.4 mm; heavy-rain day, precipitation of at least 12.7 mm on a day that is not a snow day;
  extreme-heat day, TMAX of at least 32.2 C. A flag is null when its value is missing, and that day
  is left out of the comparison instead of counted as an ordinary day.
- **Consequences:** The latest week has no weather, so the comparison covers days up to about a
  week old and says so. One station stands for the whole network (Logan is at the coast, so inland
  snowfall can differ), and the thresholds are descriptive conventions, not service rules. Both
  limits are stated with the figures. A dependency on one public endpoint, which has no SLA; when
  it is down the table stops growing and the comparison keeps its last days.
- **Revisit when:** the lag matters (add NWS observations for the newest days) or the endpoint
  changes its terms or shape.

### ADR-023: Public read-only metric API

- **Status:** Accepted (R4, Phase 4.1; built and tested, not deployed); the FastAPI half of ADR-010
- **Context:** A front end outside Streamlit (the planned Vercel app) and other consumers need the
  metrics as JSON with the same rules as the pages: coverage and freshness on every value, nothing
  shown under 50 % coverage. The smallest safe surface is the one ADR-020 already uses.
- **Decision:** `headway_api` is a FastAPI app on a Lambda (arm64, Mangum, HTTP API payload 2.0)
  behind an HTTP API Gateway, all of it behind `enable_r4` (`count = 0` when off). It reads only
  the public gold Parquet over HTTPS with DuckDB: the role has a log policy and nothing else, so
  raw, curated and the warehouse are unreachable by construction. It reuses `headway_mcp`
  (`runner`, `config`, `catalog`, the 50 % rule and the fixed-SQL approach); no caller text reaches
  the SQL, enums are `Literal` types and dates are parsed. All endpoints are `GET /v1/...`
  (`docs/API.md`). Every 200 is `{data, coverage, freshness, provenance}`: coverage (mean, min,
  withheld rows, the rule), freshness (`exported_at`, `metrics_as_of`, age), provenance (tables,
  METRICS.md anchor, row limit, `truncated`, `served_from`, the SQL). A withheld value is null with
  a `withheld_reason` next to it. Enum and date errors are 422 with the allowed values; a table the
  export lacks is a 404 `not_available`; an unreadable export is 502.
  - **Hygiene:** `Cache-Control: public, max-age=300` on 200s (gold is refreshed about every 15
    minutes; `no-store` on errors); CORS from an explicit allowlist (`API_CORS_ORIGINS`, a
    placeholder until the Vercel domain exists, a wildcard is dropped); API Gateway throttling
    (default 10 requests/s, burst 20); a CloudWatch alarm on 5xx; one JSON access log line per
    request (no client address); a 2 KiB limit on request and URL length (413); 500 rows per
    response (`truncated` says when more existed); a 5 s query timeout; OpenAPI at `/docs`.
  - **Packaging:** a container image, not a zip. Measured on 2026-11-24: the arm64 zip would be
    63 MB unpacked (23 MB zipped) plus the 19 MB `httpfs` extension, far under the 250 MB zip limit;
    the image is 900 MB (Lambda base image included), far under 10 GB. Both fit. The image wins
    because `httpfs` for linux/arm64 can only be installed by an arm64 build (a macOS zip build
    cannot), so the Dockerfile bakes it and the function only loads it (read-only root, HOME unset),
    and the image rides the existing `images-build`/`images-smoke` path. Cost: a larger image and a
    slightly longer cold start than a zip.
  - **Concurrency:** the function has `reserved_concurrent_executions = api_reserved_concurrency`
    (default 3). A reservation is taken from the account pool and Lambda keeps 100 unreserved, so at
    the 10-execution limit new accounts start with a reservation cannot be made at all, and one
    that could would starve ingest, curate and transform. `enable_r4` is therefore refused (variable validation and a Lambda
    precondition) until `api_confirmed_concurrency_limit` is set to a value of at least 100 plus the reservation (request 1000), the
    number `aws lambda get-account-settings` prints. The variable is a statement, not a lookup;
    Terraform cannot read the quota, so DEPLOY makes the check the first step.
  - **Warm-container cache:** a container downloads the small gold tables the API uses (the
    allowlist in `headway_api.queries.API_TABLES`, a few MB) to `/tmp` once per export, keyed by the
    manifest's `exported_at`, re-reads the manifest at most every 60 s, and runs queries on the
    local copy. Any failure (download, disk, a query on the copy) falls back to HTTPS reads, and a
    suspect copy is deleted so the next re-check fetches it again. Provenance SQL always names the
    public objects. Effect on S3: without the cache every request is one manifest read plus the
    range requests of DuckDB's reader for each table (footer and row groups, several GETs per file,
    each a round trip to S3). With it a warm container makes at most one manifest GET a minute and
    one GET per table (about a dozen) per export, and a request is a local file read. These counts
    follow from the design; latency was not measured against the real bucket (nothing is deployed).
    Public-bucket GETs cost a few cents per million, so the point is latency and less S3 traffic
    under throttling, not money.
- **Consequences:** The API can say nothing the public cannot already read and shares its numbers
  and rules with the pages and the analyst. `/v1` is a contract: a breaking change is `/v2`. The
  API reflects the export, so it is as fresh as gold (up to the 60 s re-check plus the 300 s cache
  header). Concurrency is capped at the reservation, so a burst beyond it gets Lambda throttling
  (surfaced as 5xx by the gateway) rather than starving the pipeline.
- **Revisit when:** traffic exceeds the throttle (a CDN in front, or longer max-age), a consumer
  needs history or station-level data beyond the countdown table, or the Next.js app needs
  authentication (none today: the data is public).

### ADR-024: Account Lambda memory cap

- **Status:** Accepted
- **Context:** This account cannot configure a Lambda above 3008 MB (the default quota for new
  accounts; it is not ours to raise from the code). The transform runs at the cap. In production it
  ran at 3005-3006 MB and then failed with `Runtime.OutOfMemory` (see
  [INCIDENTS](INCIDENTS.md#transform-out-of-memory-at-the-account-memory-cap)).
- **Decision:** treat 3008 MB as a fixed budget and bound the work to it instead of asking for more.
  Budget: peak memory at most 85% of the configured limit (2557 MB at 3008). DuckDB gets
  `memory_limit` 1500 MB and 1 thread: the process adds 700 to 800 MB on top (Python, dbt, boto3,
  Elementary, allocations DuckDB does not count), so 2200 MB with 2 threads peaked at 2917 MB (97%)
  and 1800 MB with 2 threads at 85.5% with 7 GB of spill, while 1500 MB with 1 thread peaked at
  2331 MB (77.5%) on a 7-day window with 4x vehicle sampling. DuckDB spills to `/tmp` (ephemeral storage
  10240 MB, spill cap 8 GB, `preserve_insertion_order=false`). Everything that reads a table into
  Python does so in chunks or streams to disk. The limits are variables
  (`transform_duckdb_memory_limit`, `transform_duckdb_threads`, `transform_duckdb_max_temp`) so a
  setting can be tuned without a code change, never raised past what leaves the headroom.
- **Consequences:** a slower run when a step spills (measured in
  [MEASUREMENTS](MEASUREMENTS.md#transform-memory-under-the-account-cap)); more `/tmp` use, which is
  billed above 512 MB at a small rate; a guard in `make images-smoke` and an alarm at 85%.
- **REPORT versus the metric:** after the deploy, the Lambda `REPORT` shows `Max Memory Used` of
  2992 to 3008 MB while `TransformMemoryUsedPct` is 62 to 63% and runs succeed (140 to 220 s). The
  REPORT figure includes page cache from DuckDB spilling to `/tmp` (reclaimable); the alarm
  deliberately uses process RSS, so it does not fire on the cache.
- **Incremental closed-day facts (accepted 2026-11-26):** the first transform run with Release 2
  took 453 s of the 900 s limit with 15-second vehicle sampling about to quadruple the vehicle
  data ([INCIDENTS](INCIDENTS.md#transform-duration-at-half-the-lambda-limit-caught-by-monitoring)),
  so the fallback named here was built. `int_vehicle_stop_events`, `fct_reconstructed_arrivals`,
  `fct_headways` and `fct_stop_predictions` are computed once per closed service day and persisted
  as immutable Parquet in `s3://<raw>/state/facts/<table>/metric_version=<N>/service_date=D/part.parquet`
  (conditional put, never rewritten; a version per table in `facts_state.FACT_VERSIONS`, written
  next to the old one). Each run plans the window: a day with all four partitions is read from
  state, every other day (open, or closed but not persisted, so a missed run heals itself) is
  built from raw data, and the closed ones are written afterwards. A closed day with no rows gets
  an empty partition. dbt unions the two (`fact_union`), so the window aggregates are built from
  the union of persisted and computed days; the staging tables stay whole-window because the
  quality, pipeline-health and trip tables read them directly. `rebuild_facts` backfills a range or
  a new version; `HEADWAY_INCREMENTAL_FACTS=false` builds everything as before. dbt unit tests are
  excluded from the Lambda run (they run in CI and would see the plan's variables). The curated
  window is fetched in parallel to `/tmp` before the load, because one DuckDB thread reads 786
  objects one request at a time.
  Measured on the synthetic 7-day window with 4x vehicle data at 3008 MB and 1.7 CPUs
  ([MEASUREMENTS](MEASUREMENTS.md#transform-duration-with-closed-day-facts)): 183 s full, 197 s the
  first incremental run, 102 s in steady state; peak memory 66%, 64% and 62%. An incremental build
  equals a full one in every table (`tests/unit/test_incremental_equivalence.py`). Trade-offs: a
  closed day is frozen at the first run after 06:00 local, so a capture that arrives later never
  enters it (the same rule as the history tables); and `stg_trip_updates` (25 s) and dbt's
  per-invocation overhead remain per run. The alarm `headway-transform-duration-high` watches the
  run time ([RUNBOOK](RUNBOOK.md#alarm-headway-transform-duration-high)).
- **Revisit when:** the quota is raised (then the budget scales with it and the DuckDB limit can
  rise), the 7-day window or sampling grows past what fits at the limit, a measured run needs
  more than the 85% budget, or steady-state runs approach 600 s again: the next steps are persisting
  the per-day staging aggregates (`stg_trip_updates` and the quality inputs), then a larger window
  becomes possible.
