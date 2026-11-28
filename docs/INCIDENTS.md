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

## Ingest Lambda memory underestimated

- **Symptom:** after the 15 s polling deploy the ingest function's REPORT lines showed
  `Max Memory Used: 114 MB` of 128 MB (89 %) on single-poll runs that took 4 to 6 s.
- **Root cause:** the function was sized at 128 MB from ADR-014's local smoke, which measured
  60 to 70 MB resident. That figure leaves out the Lambda runtime's own overhead, so it
  understated what Lambda reports by roughly 45 MB.
- **Fix:** within minutes the function went back to 256 MB. It now uses 127 to 128 MB (50 %) and
  a run takes 2.5 s. ADR-014 and MEASUREMENTS.md carry the numbers.
- **Guard:** `make ingest-smoke` now fails when the peak exceeds 50 % of the configured memory
  (`--limit-mb` defaults to 128, half of the 256 MB function; the smoke runs under a 256 MB
  container limit), so a measurement taken without runtime overhead cannot approve a size again.

## Elementary history not persisted: S3 AccessDenied

- **Symptom:** the transform logged `could not persist the Elementary history`:
  `AccessDenied` for `s3:ListBucket` on `headway-raw-381232271655`, for the Elementary state
  prefix. The run itself succeeded (the step never raises), but the anomaly test history was not
  appended, so it would not have survived the container.
- **Root cause:** the state module keeps its tables under `state/<folder>/` (`transform_runs`,
  `elementary_test_results`, `elementary_data_monitoring_metrics`). The transform role only
  listed, read and wrote `state/transform_runs/`; the Elementary folders were added later
  without a matching policy change.
- **Fix:** the transform policy takes one list of state prefixes (`transform_runs` and
  `elementary_*`) and uses it for the `s3:ListBucket` prefix condition, `s3:GetObject` and
  `s3:PutObject`. Terraform validates; it ships with the Release 2 deploy, not yet applied.
- **Guard:** `tests/unit/test_transform_state_iam.py` checks that every folder of every persisted
  state table is covered for list, get and put, and that the state module uses no other S3 action.

## images-smoke flaky: leaked moto servers and host sleep

- **Symptom:** `make transform-smoke` failed now and then in Phase 3.5 and passed on retry. Five
  fresh runs on 2026-11-24 all passed, but one took 1007 s instead of 135 s.
- **Evidence:** after those runs, `ps` showed about 40 `moto.server` processes, the oldest from
  the previous Monday, each holding a port in 9800-9949. The script picked `9800 + RANDOM % 150`,
  started moto with its output discarded, and only waited for *something* to answer on the port.
  With a quarter of the range taken, a pick often hit an old server: the new one failed to bind
  unseen, the wait succeeded against the old one, and the run used its leftover buckets and
  history (the history-immutability and `history_written > 0` checks then fail). The leak is
  from the exit trap, which killed the `uv run` wrapper and not the server under it. The 1007 s
  run matches `pmset -g log`: the Mac idle-slept for 874 s in the middle of it; a second sleep
  later made the 900 s request timeout fire (curl exit 28).
- **Root cause:** the smoke trap leaked a moto server per run and the port choice and readiness
  check could not tell a fresh server from a stale one. Host sleep was a separate cause of
  stalls. Not a product bug: nothing in the image changed.
- **Fix (`scripts/smoke_image.sh`):** moto starts as the python process itself on a port the OS
  confirms free, so the trap kills the real server; the run fails if moto does not start or
  already holds buckets; the emulator wait fails instead of falling through; curl has timeouts;
  the script re-runs itself under `caffeinate -i`; and a preflight needs 6 GB free disk and 3 GB
  Docker memory. Stale servers were killed by hand once.
- **Also:** the smoke now runs the `analyst_examples.json` export step (it was untested) and
  reads the object back from S3.
- **Guard:** the checks above are in the script, so a leaked server or stale state fails fast
  with its cause.

## Transform out of memory at the account memory cap

- **Symptom:** the hourly transform failed with `Runtime.OutOfMemory` at 02:20 UTC on 25 Nov
  (`headway-transform-errors`). The `Max Memory Used` of the previous runs had been 3005-3006 MB
  of the 3008 MB limit.
- **Timeline (UTC):** the runs before 25 Nov 02:20 finished at 3005-3006 MB with no alarm (the
  limit was not far enough off to look like a problem and nothing watched memory); 02:20 the run
  hit the limit and was killed; the account cannot be given more than 3008 MB per function, so the
  limit could not be raised; the fix and guard below were built the same day and not deployed from
  this change.
- **Root cause:** no DuckDB memory limit anywhere. DuckDB sizes itself from the host's RAM, not
  the function's cgroup, so each connection (dbt's, plus about 16 more in the transform package)
  was allowed about 80% of the host and four dbt threads built every table in memory. Two things
  grew the working set: the 7-day window (`LOOKBACK_DAYS`) filled up, and Release 2 quadruples
  vehicle rows (ADR-014). The account's 3008 MB cap removed the usual escape of a bigger function.
  Python added to it: the forecast serving features were read whole into a DataFrame, history and
  state objects were read whole into bytes, and the Elementary report runs in the same process.
- **Fix:** every connection goes through `headway_transform.duckdb_limits.connect`
  (`memory_limit` 1500 MB, 1 thread, spill to `/tmp/duckdb_spill`, 8 GB spill cap,
  `preserve_insertion_order=false`; all from `HEADWAY_DUCKDB_*` environment variables, the same
  in the dbt `lambda` profile); transform ephemeral storage 4096 to 10240 MB; forecast scoring in
  chunks with per-slot partial sums; history and state objects streamed to and from disk. The run
  reports `peak_memory_mb`, `step_peak_mb` and `spill_peak_mb`. Measured with `scripts/measure_transform.py`: before, the unbounded setting was killed at the 3008 MB
  limit in 5 s on the real data, and 2200 MB / 2 threads still peaked at 97%; after, real data peaks at
  2052 MB (68%) in 67 s and a synthetic 7-day window with 4x vehicle sampling at 2331 MB (77.5%) in 172 s
  (the numbers are in
  [MEASUREMENTS.md](MEASUREMENTS.md#transform-memory-under-the-account-cap).
- **Guard:** `make images-smoke` runs the transform in a container with `--memory` equal to the
  function's configured memory and fails when its peak is above 85% of it; the run emits
  `TransformMemoryUsedPct` and the alarm `headway-transform-memory-high` fires above 85%
  ([RUNBOOK](RUNBOOK.md#alarm-headway-transform-memory-high)); `tests/unit/test_duckdb_limits.py`
  fails on any unbounded `duckdb.connect` in the package. See
  [ADR-024](ARCHITECTURE_DECISIONS.md#adr-024-account-lambda-memory-cap).

## Transform duration at half the Lambda limit, caught by monitoring

A near miss: nothing failed.

- **Symptom:** on 26 Nov, the first transform run with Release 2 on took 453 s of the 900 s
  Lambda limit (`s3_load` 109 s, `dbt_build` 230 s, `dbt_test` 69 s), on 1-minute data. 15-second
  vehicle sampling had just started, so vehicle data was about to grow 4x as the 7-day window
  filled over the following week, and Lambda runs about 5x slower than local estimates.
- **How it was found:** by reading the run summary (`step_ms`) in the logs while watching the
  switch-on, before the window filled. No alarm covered duration: the existing ones fire on
  errors, staleness and memory, and a run killed at 900 s emits nothing.
- **Root cause:** every hourly run rebuilt the heavy facts (`int_vehicle_stop_events`,
  `fct_reconstructed_arrivals`, `fct_headways`, `fct_stop_predictions`) for all 7 days, although
  a closed service day never changes; and `s3_load` read the window's 786 objects (70 MB) through
  DuckDB's S3 client on one thread, so its time was request latency (a serial GET measured about
  100 ms from a laptop; in-region it is lower, and there are several requests per object).
- **Fix:** closed-day facts are computed once and kept in `state/facts/` (ADR-024); each run
  builds only the days that have no persisted partition. The curated window is fetched with
  one GET per object, 16 at a time, before the load. Measured on a synthetic 7-day window with
  4x vehicle data at 3008 MB and 1.7 CPUs: 183 s for a full rebuild, 102 s for an incremental
  run, peak memory 66% and 62%
  ([MEASUREMENTS](MEASUREMENTS.md#transform-duration-with-closed-day-facts)).
- **Guard:** the transform emits `TransformDurationSeconds` for every scheduled run and the
  alarm `headway-transform-duration-high` fires when two runs in a row exceed 600 s
  ([RUNBOOK](RUNBOOK.md#alarm-headway-transform-duration-high)); the summary line lists the days
  computed and the days loaded from state, and the prefetch's object count and time;
  `tests/unit/test_incremental_equivalence.py` fails when an incremental build differs from a full
  one in any table, and `tests/unit/test_facts_state.py` fails when a day that closes between two
  runs, or after a missed run, is not persisted exactly once.

## dbt unit tests failed inside the Lambda after the first Release 2 run

- **Symptom:** `headway-transform-errors` went to ALARM on 26 Nov at 18:29 UTC; the 18:00 slot had no
  gold export and the 19:00 slot failed the same way until a run after the deploy.
- **Root cause:** the Lambda ran the dbt unit tests, which with Release 2 on also see persisted
  history; the first R2 run wrote history, and from the next run three unit tests failed on those
  real rows, four models were skipped and `export_gold` found their tables missing.
- **Fix:** unit tests no longer run in the Lambda (`728643f`, deployed 19:38 UTC); they run in CI.
- **Detail:** request ids, times and the slot accounting are in
  [MEASUREMENTS](MEASUREMENTS.md#release-2-soak-transform-slots-since-switch-on-investigated-2026-11-28).

## Capture completeness read 37-59% while every feed was fetched

- **Symptom:** the 7- and 30-day SLO report showed capture completeness at 79 % (58.8 % on 26 Nov,
  37.6 % on 27 Nov) and `agg_pipeline_health.vehicle_positions_status` null in most minutes since
  15-second sampling began (26 Nov about 18:00 UTC), while the raw manifests had every feed `ok`.
  `feed_gap_min` failed in 13, 16 and 7 hours of the three days for the same reason.
- **Found by:** reading the public gold against the raw manifests while checking the transform
  slots (MEASUREMENTS), then the curated manifests: 1,862 of 3,040 runs had no vehicle poll 0.
- **Root cause:** a capture id is the feed, the fetch second and the payload hash. The ingest Lambda
  starts about 15 seconds into its minute, so vehicle poll 1 follows poll 0 within a second and, with
  an unchanged payload, has the same id. Curation keyed manifest entries by `(run_id, capture_id)`
  and the later poll replaced poll 0. Poll 0 is the minute's status for every feed, so the minute had
  none. The data, the raw manifests and every payload table were intact.
- **Fix:** manifest entries are keyed by `(run_id, capture_id, poll_index)` in curation and its
  backfill; staging keeps one row per capture id with `n_polls`, so a minute's vehicle status is
  that of poll 0 and polls are counted exactly (`vehicle_polls_ok`, `vehicle_polls_error`,
  `n_captures`). `backfill_manifests` restores the curated hours from raw (DEPLOY, step A2).
- **Guard:** `manifest_runs_have_poll_zero_of_every_feed` (a delivered run has poll 0 of all three
  feeds), `capture_health_matches_manifests` and `capture_slo_matches_manifests` (the pipeline
  health and the SLO count the manifests' own minutes), dbt unit tests with a 15-second minute and
  a 1-minute minute for staging, pipeline health and the SLO, and curation tests for polls that
  share an id. Measurements before and after:
  [MEASUREMENTS](MEASUREMENTS.md#capture-completeness-in-the-public-slo-cause-and-corrected-figures-2026-11-28).

## Drills

Failures caused on purpose with the chaos switch ([ADR-017](ARCHITECTURE_DECISIONS.md), how to
start and stop one in [RUNBOOK.md](RUNBOOK.md#live-drills)). A drill is written up here so it is
never mistaken for an incident: every entry is titled `DRILL`, and a real incident never is.
The numbers a drill leaves in the data (data-outage hours, spent error budget, a failed transform
slot) are real and stay in the history; this section is how to tell them apart.

Format, one entry per drill, newest last:

```
### DRILL YYYY-MM-DD: <mode> for <minutes> min

- **Window:** start and end in UTC (the `until` that was set), who ran it.
- **Mode and why:** `feed_down` or `md_down`, and what it was meant to prove.
- **Expected:** what should fire or change (alarms, quality check status, labelled hours).
- **Observed:** what did, with the times; note anything that did not fire or fired late.
- **Recovery:** when the first clean minute or run was seen, and how it was confirmed.
- **Follow-ups:** changes to alarms, thresholds or this runbook, or `none`.
```

No drill has been run yet.

