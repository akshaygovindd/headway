# Production incidents

Technical incidents found while bringing the pipeline up in AWS: what was seen, why, what changed,
and what now prevents a repeat. Newest last.

## Scheduler input placeholder not substituted

- **Symptom:** the ingest Lambda received the literal text `<aws.scheduler.scheduled-time>` as
  `scheduled_at`, so runs were stamped with a value that does not parse.
- **Root cause:** the schedule input was built with Terraform `jsonencode()`, which escapes `<`
  and `>` as `<` / `>`; EventBridge Scheduler only substitutes the placeholder when it is
  written literally.
- **Fix:** the input is a literal string in `infra/main/scheduler.tf`. Ingest also falls back to
  the current minute, with a warning, when `scheduled_at` cannot be parsed.
- **Guard:** `tests/unit/test_handler.py` covers the literal placeholder and a missing value.

## Lambda asynchronous retries

- **Symptom:** failed invocations were re-run automatically, so one scheduled minute could produce several runs.
- **Root cause:** Lambda retries failed asynchronous invocations (twice by default) and
  EventBridge Scheduler has its own retry policy; the pipeline functions are not safe to re-run
  blindly and the next scheduled run replaces a missed one anyway.
- **Fix:** `maximum_retry_attempts = 0` on every schedule and on the curate, static and transform
  functions' event invoke config.
- **Guard:** `tests/unit/test_scheduler_config.py` fails if a schedule lacks the zero-retry policy.

## Static GTFS load timeout

- **Symptom:** the static Lambda ran past 300 s without finishing.
- **Root cause:** the full feed was loaded row-wise and unfiltered; only afterwards was it reduced
  to the subway routes in scope.
- **Fix:** tables are streamed with `pyarrow.csv`, typed by their curated schema and filtered to
  the in-scope routes before further work; the load takes 2.5 s.
- **Guard:** `tests/unit/test_curate_static.py`.

## DuckDB home and extension directories

- **Symptom:** dbt could not start in Lambda: it failed while connecting and installing the
  `httpfs` extension.
- **Root cause:** the Lambda root filesystem is read-only and `HOME` is unset, so DuckDB's
  default home (`~/.duckdb`) and extension directory are not writable. dbt-duckdb installs
  extensions right after connecting, before profile `settings` apply.
- **Fix:** `HOME` defaults to `/tmp`, and the profile passes `home_directory` and
  `extension_directory` as connect-time `config_options`; extensions are baked into the image.
- **Guard:** the image smoke test (`make images-smoke`) runs the handler under Lambda conditions:
  read-only root, writable `/tmp` only, `HOME` unset.

## Missing /dev/shm

- **Symptom:** dbt failed on startup with an `OSError` (`ENOENT`) creating a lock.
- **Root cause:** Lambda has no `/dev/shm`, so POSIX semaphores (`sem_open`) cannot be created and
  dbt's `multiprocessing` locks fail.
- **Fix:** when a semaphore cannot be created, the handler substitutes thread locks (dbt runs in
  one process).
- **Guard:** the image smoke test; the patch only applies when a semaphore genuinely fails.

## A failing test skipped the gold models

- **Symptom:** one failing dbt test and the aggregate models downstream of it were missing from
  gold, with no error.
- **Root cause:** `dbt build` skips every model downstream of a failed test.
- **Fix:** models are built without tests (`dbt build --exclude resource_type:test`) and tests run
  as their own step; failures are counted and logged, and the export continues.
- **Guard:** `test_test_failures_do_not_block_export` in `tests/unit/test_transform_handler.py`.

## MotherDuck extension directory was read-only

- **Symptom:** the publish step failed loading the `motherduck` extension.
- **Root cause:** the extension downloads its implementation when it loads, into the extension
  directory; the baked directory sits in the read-only image.
- **Fix:** at start-up the baked extensions are copied to `/tmp/duckdb_extensions` once per
  container and every connection (dbt profile, publish) uses the copy.
- **Guard:** `assert_extension_dir_writable` runs in the image smoke test;
  `test_prepare_extension_dir_copies_baked_extensions_once`.

## DuckDB version not supported by MotherDuck

- **Symptom:** the publish step failed with "not yet supported ... We recommend DuckDB v1.5.6"
  on DuckDB 2.0.1.
- **Root cause:** the `motherduck` extension only loads on the engine versions MotherDuck
  supports, which lag the newest DuckDB.
- **Fix:** `duckdb==1.5.6` is pinned in the dbt environment and the root environment; the image
  bakes extensions for that version.
- **Guard:** the image build asserts the version; `tests/unit/test_dockerfiles.py`.

## JSON timestamps typed differently across DuckDB versions

- **Symptom:** manifest timestamps were typed differently under DuckDB 1.5 and 2.0, and a
  null-check test failed on 1.5 with a syntax error.
- **Root cause:** `read_json_auto` infers `...Z` strings as naive `TIMESTAMP` on 1.5 and
  `TIMESTAMPTZ` on 2.0. A related macro rendered `where ()` for a test with no `where` config,
  which 1.5 rejects.
- **Fix:** columns are declared explicitly (`TIMESTAMPTZ`), and the curated manifests/quarantine
  Parquet carries an explicit UTC schema; the test macro no longer takes a `where` argument.
- **Guard:** `tests/unit/test_dbt_macros.py`; `tests/unit/test_curate_meta.py` checks the Parquet
  schema.

## Transform scaling over S3 views, and overlapping runs

- **Symptom:** a trivial dbt test took 50-110 s with one day of data, and the transform
  approached its timeout. A manual run overlapping the scheduled one doubled the load.
- **Root cause:** realtime staging models were views over S3, so every downstream model and test
  re-scanned the files, and run manifests were one raw JSON object per run (116,845 S3 requests in
  the benchmark). Nothing prevented two transform runs at once.
- **Fix:** staging and intermediate models are tables (`s3_load` builds once); manifests and
  quarantine are also curated as hourly Parquet (143 requests); a run takes an S3 lock object
  with a conditional put (stale after 15 minutes); the function timeout is 900 s and each step is
  timed.
- **Guard:** the lock tests in `tests/unit/test_transform_handler.py`; per-step timings in the
  summary line; `scripts/bench_transform.py` for regressions.

## Duplicate deliveries

- **Symptom:** three dbt tests failed on production data: `unique_grain` on `stg_manifests`
  and on `int_captures` (3 duplicate `capture_id`s each) and on `fct_stop_predictions`
  (1,444 duplicate rows from the join).
- **Root cause:** EventBridge Scheduler delivers at least once. For the minute 2026-11-22T16:35Z
  four runs wrote manifests; two started 0.35 s apart and shared every `capture_id` (same fetch
  second, same payload). The manifest key was derived from each run's own id, so nothing stopped
  both from writing, and `stg_manifests` deduplicated only on `run_id x capture_id`.
- **Fix:** the manifest key is derived from the scheduled minute and written with a conditional
  create (`IfNoneMatch='*'`). The delivery that loses writes a duplicate-delivery record under
  `manifests/<city>/duplicates/`, logs it and returns normally. Curation flags those records in
  the manifests table and never curates their captures. `stg_manifests` keeps the earliest
  `fetched_at` per `capture_id` (ties: lowest `run_id`); `agg_pipeline_health` reports
  `duplicate_delivery_count` and `agg_quality_checks_hourly` has a `duplicate_deliveries` check
  (warn 1, fail 5 per hour).
- **Guard:** concurrent-delivery tests in `tests/unit/test_capture.py` and
  `tests/unit/test_handler.py` (one manifest plus one record); the e2e run contains both a history
  twin and a flagged record; `unique_grain` tests stay on.

## Cost report connection conflict

- **Symptom:** the cost step logged `ConnectionException: Can't open a connection to same database
  file with a different configuration than existing connections`, and `cost_daily.parquet` was not
  written.
- **Root cause:** dbt-duckdb keeps its read-write connection to `/tmp/headway.duckdb` open for the
  life of the process; the cost step opened the same file read-only, which DuckDB refuses within
  one process.
- **Fix:** the handler closes dbt's connection (`release_dbt_connections`) before the cost step
  opens the file. The report still only runs on the first transform run at or after 06:00 UTC.
- **Guard:** `test_cost_reads_the_database_dbt_still_holds_open_after_release` reproduces the
  conflict and the release; `test_cost_report_is_written_after_dbt_connections_are_released`
  pins the order.
