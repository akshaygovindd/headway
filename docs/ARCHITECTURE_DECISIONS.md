# Architecture Decisions

Written during Release 1.

## Entry template

### ADR-NNN: Title

- **Status:** Proposed | Accepted | Superseded
- **Context:** What forces are at play?
- **Decision:** What was decided?
- **Consequences:** What follows from it?
- **Revisit when:** What would make us reconsider?

### ADR-012: dbt compute in Lambda DuckDB, MotherDuck as published warehouse

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
