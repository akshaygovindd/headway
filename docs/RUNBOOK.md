# Runbook

What to do when an alarm fires or a quality check or objective goes red, and how to rehearse the
failures on purpose. One section per CloudWatch alarm, then one per simulated failure (the six
replay simulations in `tests/simulations/` and, for two of them, a live drill).

Conventions. Commands are read-only unless a **Fix** says otherwise. Run them from the repository
root with credentials for the account. Alarms notify the `headway-alerts` SNS topic by email.

```sh
RAW=$(terraform -chdir=infra/main output -raw raw_bucket)
PUBLIC=$(terraform -chdir=infra/main output -raw public_bucket)
```

Past incidents and their root causes are in [INCIDENTS.md](INCIDENTS.md); design background in
[ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md) and the objectives in [SLO.md](SLO.md).

- [Alarms](#alarms)
- [Simulations](#simulations)
- [Training job](#training-job)
- [Live drills](#live-drills)

## Alarms

### Alarm: headway-ingest-rt-errors

- **Meaning:** the ingest Lambda (once a minute) failed 3 or more times in 15 minutes. A failed
  *fetch* is not a Lambda error (it is recorded as an `error` capture, see
  [Feed down](#simulation-feed-down)); this alarm means the function itself raised: a storage
  write, the live snapshot write, or a crash. Every failed minute is a missing or partial
  manifest.
- **First 3 commands:**
  ```sh
  aws logs tail /aws/lambda/headway-ingest-rt --since 30m --filter-pattern '?ERROR ?Traceback ?Exception'
  aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Errors --dimensions Name=FunctionName,Value=headway-ingest-rt --start-time $(date -u -v-1H +%FT%TZ) --end-time $(date -u +%FT%TZ) --period 300 --statistics Sum
  aws s3 ls "s3://$RAW/manifests/boston/dt=$(date -u +%F)/" --recursive | tail -5
  ```
- **Likely causes:** the S3 write was refused (IAM or a deleted bucket), the live snapshot write
  failed (the manifest is still written, then the error is raised so it alarms), a bad deploy of
  the zip, or the scheduler delivering the placeholder text
  ([Scheduler input placeholder](INCIDENTS.md#scheduler-input-placeholder-not-substituted),
  [Lambda asynchronous retries](INCIDENTS.md#lambda-asynchronous-retries)).
- **Fix:** read the traceback. A permissions or bucket problem: restore it with `terraform apply`
  (review the plan first). A bad zip: redeploy the previous one (`make lambda-zip`, `make
  deploy`). Nothing needs replaying: the next scheduled minute captures current data, and the lost
  minutes show as missed runs ([Missed invocations](#simulation-missed-invocations)).
- **Recovery:** `aws logs tail /aws/lambda/headway-ingest-rt --since 5m` shows one summary line
  per minute with `statuses` all `ok`; the alarm returns to OK within 15 minutes; the Pipeline
  health page shows no new missed runs.

### Alarm: headway-feed-errors

- **Meaning:** at least one realtime feed (trip updates, vehicle positions or alerts) failed to
  fetch in 5 or more of the last 10 minutes. Ingest writes `FeedErrorCount` per feed per run as a
  CloudWatch embedded-metric log line (namespace `Headway`, dimension `Feed`; 1 when the minute's
  first fetch of that feed failed). A failed fetch is not a Lambda error, so this is the only
  alarm that sees a feed outage ([Feed down](#simulation-feed-down)). Missing data does not
  breach: a silent ingest is [headway-ingest-rt-invocations-low](#alarm-headway-ingest-rt-invocations-low).
- **First 3 commands:**
  ```sh
  aws logs tail /aws/lambda/headway-ingest-rt --since 15m --filter-pattern '{ $.FeedErrorCount > 0 }'
  curl -s -o /dev/null -w '%{http_code}\n' https://cdn.mbta.com/realtime/VehiclePositions.pb
  aws ssm get-parameter --name /headway/chaos --query Parameter.Value --output text
  ```
- **Likely causes:** the MBTA feed or the network is down, the feed URL or an API key changed, or
  a live `feed_down` [drill](#live-drills) is running (the last command shows an active switch).
- **Fix:** nothing on our side if the feed is down; if the URL changed, update
  `config/cities/boston.yaml` and redeploy. If a drill is running, stop it.
- **Recovery:** the `FeedErrorCount` lines are 0 again, the alarm returns to OK once fewer than 5
  of the last 10 minutes had errors, and the ingest summary lines show `statuses` all `ok`. Hours
  without enough captures stay labelled `data_outage`.

### Alarm: headway-ingest-rt-invocations-low

- **Meaning:** the ingest Lambda ran fewer than 10 times in 15 minutes (about 15 expected). The
  schedule is not firing, or the function is throttled or disabled. Missing data (no metric at
  all) also breaches. Minutes that never run are never captured: they are missed runs, and 3 in
  an hour make the hour a data outage.
- **First 3 commands:**
  ```sh
  aws scheduler get-schedule --name headway-ingest-rt-every-minute --query '{State:State,Expr:ScheduleExpression}'
  aws lambda get-function --function-name headway-ingest-rt --query 'Configuration.{State:State,LastUpdate:LastUpdateStatus,Concurrency:Concurrency}'
  aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Throttles --dimensions Name=FunctionName,Value=headway-ingest-rt --start-time $(date -u -v-1H +%FT%TZ) --end-time $(date -u +%FT%TZ) --period 300 --statistics Sum
  ```
- **Likely causes:** the schedule was disabled or deleted, the scheduler role lost its permission
  to invoke, the function is being updated or throttled by the account concurrency limit, or an
  AWS-side event. See [Lambda asynchronous retries](INCIDENTS.md#lambda-asynchronous-retries) for
  why a failed minute is never retried.
- **Fix:** if the schedule is `DISABLED` or missing, `terraform apply` restores it. If the role
  is wrong, the same. Do not invoke the function by hand in a loop to fill the gap: a manual
  invoke stamps the current minute, not the lost ones.
- **Recovery:** invocations return to about 15 per 15 minutes; the new manifests appear in
  `aws s3 ls "s3://$RAW/manifests/boston/dt=$(date -u +%F)/" --recursive | tail`; the gap stays
  in the data as `missed_runs` and, if 3 or more fell in an hour, as a `data_outage` hour.

### Alarm: headway-curate-errors

- **Meaning:** the curate Lambda (every 15 minutes, plus hourly compaction) failed at least once
  in 15 minutes. Curation turns raw captures into the Parquet the transform reads, so a stuck
  curate means gold stops advancing, while capture continues safely in the raw bucket.
- **First 3 commands:**
  ```sh
  aws logs tail /aws/lambda/headway-curate --since 1h --filter-pattern '?ERROR ?Traceback ?Exception'
  aws lambda get-function-configuration --function-name headway-curate --query '{Image:CodeSha256,Timeout:Timeout,Memory:MemorySize,Env:Environment.Variables}'
  aws s3 ls "s3://$RAW/curated/boston/" --recursive | tail -5
  ```
- **Likely causes:** a malformed capture the parser cannot quarantine, a timeout on a large
  backlog, a read-only or full `/tmp`, a deploy that changed the schema. A corrupt single feed
  object is normally quarantined rather than failing the run
  ([Duplicate deliveries](INCIDENTS.md#duplicate-deliveries) for what a duplicate record looks
  like to curation).
- **Fix:** curation is incremental and idempotent (a watermark moves only after a group is
  written), so the fix is to correct the cause and let the next run, or an explicit
  `aws lambda invoke --function-name headway-curate --cli-binary-format raw-in-base64-out
  --payload '{}' /dev/stdout`, catch up. To rebuild the manifest tables from raw JSON use the
  `backfill_manifests` task in [DEPLOY.md](DEPLOY.md#operations).
- **Recovery:** the next curate summary line reports `batches` and no error; new files appear
  under `curated/boston/`; the alarm clears at the next 15-minute window.

### Alarm: headway-static-errors

- **Meaning:** the static GTFS Lambda (daily at 09:00 UTC) failed in the last day. The schedule
  the metrics compare against may be out of date; nothing is lost, the previous feed version
  stays in use.
- **First 3 commands:**
  ```sh
  aws logs tail /aws/lambda/headway-static --since 26h --filter-pattern '?ERROR ?Traceback ?Exception'
  aws s3 ls "s3://$RAW/curated/boston/static/table=feed_info/" --recursive
  aws lambda get-function-configuration --function-name headway-static --query '{Timeout:Timeout,Memory:MemorySize,Env:Environment.Variables}'
  ```
- **Likely causes:** the MBTA GTFS download failed or changed shape, a timeout
  ([Static GTFS load timeout](INCIDENTS.md#static-gtfs-load-timeout)), or a feed whose
  `feed_info.txt` has no `feed_version`. A new feed version that changes trip ids is a
  [GTFS static update](#simulation-gtfs-static-update) and is safe by design.
- **Fix:** re-invoke after the cause is gone: `aws lambda invoke --function-name headway-static
  --cli-read-timeout 330 --cli-binary-format raw-in-base64-out --payload '{}' /dev/stdout`
  (an already loaded version is skipped).
- **Recovery:** the summary shows the loaded `feed_version` (or `skipped`), a new
  `feed_version=` folder exists when the MBTA published one, and `schedule_join_coverage` stays
  at or above 90% in the hourly checks.

### Alarm: headway-transform-errors

- **Meaning:** the transform Lambda (hourly at :20) failed at least once in the hour. The run
  builds the models, exports gold to S3 and publishes to MotherDuck; a failure at any step is an
  error. The step that failed is in the summary line.
- **First 3 commands:**
  ```sh
  aws logs tail /aws/lambda/headway-transform --since 2h --filter-pattern '?ERROR ?Traceback ?publish_error ?TransformError'
  aws s3 ls "s3://$PUBLIC/public/boston/gold/" --recursive | head -30
  aws s3 cp "s3://$PUBLIC/public/boston/gold/manifest.json" - | head -c 400
  ```
- **Likely causes:** MotherDuck unavailable or the token rotated (gold is still exported first:
  [MotherDuck unavailable](#simulation-motherduck-unavailable)); a model error after a deploy; a
  timeout (900 s) on a large backlog; a stale lock after a crash
  ([Transform scaling](INCIDENTS.md#transform-scaling-over-s3-views-and-overlapping-runs),
  [Cost report connection conflict](INCIDENTS.md#cost-report-connection-conflict),
  [DuckDB version not supported by MotherDuck](INCIDENTS.md#duckdb-version-not-supported-by-motherduck)).
- **Fix:** a publish error needs no replay: the next run exports and publishes again. A model
  error: fix and redeploy. A lock held by a dead run replaces itself after 15 minutes. To run now:
  `aws lambda invoke --function-name headway-transform --cli-read-timeout 930
  --cli-binary-format raw-in-base64-out --payload '{}' /dev/stdout`.
- **Recovery:** the next summary line has `dbt_status` and no `publish_error`; `gold_published`
  is true in the run state, so the slot counts good for the transform-success objective (the
  failed slot stays bad in the history of that day); the alarm clears after the next successful
  hour.

### Alarm: headway-transform-not-succeeded

- **Meaning:** no transform run completed successfully in 2 hours (successful runs =
  invocations minus errors, missing data counts as breaching). Gold is stale: the app's "gold
  tables exported" line grows, the gold-freshness objective starts spending budget, and the
  alarm is the escalation of a transform-errors alarm that was not fixed, or of a schedule that
  stopped.
- **First 3 commands:**
  ```sh
  aws scheduler get-schedule --name headway-transform --query '{State:State,Expr:ScheduleExpression}'
  aws logs tail /aws/lambda/headway-transform --since 3h --filter-pattern '?ERROR ?skipped ?Traceback'
  aws s3 ls "s3://$RAW/state/boston/"
  ```
- **Likely causes:** repeated failures of the cause behind
  [headway-transform-errors](#alarm-headway-transform-errors); a schedule that is disabled; a
  lock that every run finds fresh (look for `{"skipped": "locked"}` lines).
- **Fix:** as for the errors alarm; if only `skipped: locked` lines appear, wait 15 minutes for
  the stale-lock rule or delete `state/boston/transform.lock` once you are sure no run is active
  (`aws s3 rm "s3://$RAW/state/boston/transform.lock"` changes the account: confirm first).
- **Recovery:** one successful scheduled run; `manifest.json` in the gold folder has a fresh
  `exported_at`; the next hour start counts as good for gold freshness (an hour is good when the
  newest export is at most 90 minutes old).

### Alarm: headway-gold-stale

- **Meaning:** the transform ran, but replaced gold more than 90 minutes after the previous
  export in two hourly periods in a row. Missing data keeps the alarm's state (the first run
  after a deploy has no previous export and emits nothing); a transform that stopped running is
  `headway-transform-not-succeeded`. The transform writes the metric as a CloudWatch embedded-metric log line
  after each export: the age, in minutes, of the export it just replaced. Gold on the site is
  older than the hourly cadence promises; the gold-freshness objective spends budget.
- **First 3 commands:**
  ```sh
  aws logs tail /aws/lambda/headway-transform --since 3h --filter-pattern '?GoldAgeMinutes ?ERROR ?skipped'
  aws s3 cp "s3://$PUBLIC/public/boston/gold/manifest.json" - | head -c 300
  aws scheduler get-schedule --name headway-transform --query '{State:State,Expr:ScheduleExpression}'
  ```
- **Likely causes:** the transform failed or timed out for a few hours
  ([headway-transform-errors](#alarm-headway-transform-errors)), the schedule is disabled, every
  run found a fresh lock, or the export step is the one that fails (look for `ExportError`).
- **Fix:** as for [headway-transform-not-succeeded](#alarm-headway-transform-not-succeeded):
  find the failing step in the logs and fix it. Nothing needs replaying; the next run exports
  everything again.
- **Recovery:** one successful scheduled run; `exported_at` in the gold `manifest.json` is
  fresh; the next two `GoldAgeMinutes` values are at most 90 (about 60 when on schedule).

### Alarm: headway-missed-minutes

- **Meaning:** 5 or more of the 60 minutes ending at the newest curated minute have no ingest
  manifest. The transform counts them from the curated manifests and writes
  `MissedRunsLastHour` as an embedded-metric log line. Gaps this size cost capture completeness
  and, past 5 minutes without fresh data, label the hour a data outage. Missing data does not
  breach: a transform that stopped is covered by the two gold alarms.
- **First 3 commands:**
  ```sh
  aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Invocations --dimensions Name=FunctionName,Value=headway-ingest-rt --start-time $(date -u -v-2H +%FT%TZ) --end-time $(date -u +%FT%TZ) --period 300 --statistics Sum
  aws scheduler get-schedule --name headway-ingest-rt --query '{State:State,Expr:ScheduleExpression}'
  aws logs tail /aws/lambda/headway-ingest-rt --since 1h --filter-pattern '?ERROR ?Task timed out ?Throttl'
  ```
- **Likely causes:** the ingest schedule was disabled or throttled, an ingest deploy broke the
  function ([headway-ingest-rt-errors](#alarm-headway-ingest-rt-errors)), or a scheduler outage
  ([Missed invocations](#simulation-missed-invocations)). Curation lag does not count: the window
  follows the newest curated minute.
- **Fix:** restore the schedule or the function (`terraform apply` after reviewing the plan; it
  changes the account, so confirm first). Lost minutes cannot be recovered; they stay in that
  hour's `missed_runs`.
- **Recovery:** the Invocations count is back to about 5 per 5 minutes; after the next curation
  and transform run `MissedRunsLastHour` drops below 5 as the gap leaves the 60-minute window.

### Alarm: headway-arrival-coverage-low

- **Meaning:** Release 2 only (`enable_r2`). Fewer than 70% of the scheduled stops of the last
  complete local hour had a usable (high or medium confidence) reconstructed arrival, three
  hours in a row. The transform writes `ArrivalCoveragePct` as an embedded-metric log line in
  service hours (05:00 to 01:00 local); outside them there is no data and the alarm keeps its
  state. Release 2 metrics built on arrivals are thin for these hours.
- **First 3 commands:**
  ```sh
  aws logs tail /aws/lambda/headway-transform --since 4h --filter-pattern ArrivalCoveragePct
  aws s3 cp "s3://$PUBLIC/public/boston/gold/manifest.json" - | head -c 300
  aws logs tail /aws/lambda/headway-ingest-rt --since 15m --filter-pattern '?VehiclePollsCompleted ?CHAOS'
  ```
- **Likely causes:** vehicle positions sampled less often (`VEHICLE_POLLS` back at 1, see
  [ADR-014](ARCHITECTURE_DECISIONS.md#adr-014-vehicle-positions-sampled-every-15-s)), a feed
  outage that covered the hours ([Feed down](#simulation-feed-down)), a GTFS static update that
  changed trips ([GTFS static update](#simulation-gtfs-static-update)), or a service disruption.
- **Fix:** restore the sampling or the feed; for a static update make sure the new feed was
  curated. Past hours are not recomputed, only history for closed days can be rebuilt
  (`rebuild_history`).
- **Recovery:** the next `ArrivalCoveragePct` values are 70 or more and the alarm returns to OK
  after the first good hour.

### Budget forecast alert

AWS Budgets also mails `budget_email` when the month's **forecast** passes USD 15, besides the
actual-spend alerts at USD 8 and 15. It is not a CloudWatch alarm (no SNS, no runbook anchor of
its own); the steps are these. **Meaning:** at the current rate the month will cost more than
USD 15. **First 3 commands:**

```sh
aws ce get-cost-and-usage --time-period Start=$(date -u +%Y-%m-01),End=$(date -u +%F) --granularity DAILY --metrics UnblendedCost --group-by Type=DIMENSION,Key=SERVICE
aws s3 cp "s3://$PUBLIC/public/boston/gold/cost_daily.parquet" /tmp/cost_daily.parquet
aws budgets describe-budget --account-id $(aws sts get-caller-identity --query Account --output text) --budget-name headway-monthly
```

**Likely causes:** a Lambda in a retry loop, `VEHICLE_POLLS=4` with a larger memory size, a
growing log group, or a drill left on. **Fix:** remove the cause; if it is the design, raise
`budget_limit_usd` with a note in [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md).
**Recovery:** the forecast drops under USD 15 after a few days at the lower rate.

## Simulations

Each of the six failures below is replayed in CI by `tests/simulations/` against the real ingest,
curation and dbt code (no AWS): it asserts that the failure is detected, that the affected hours
are labelled and kept out of the metrics, and that the pipeline recovers. The sections say what
the drill means in production and how to confirm recovery there. Where a failure can be caused
on purpose in the deployed pipeline, the section links to its [live drill](#live-drills).

### Simulation: feed down

- **Replay:** `tests/simulations/test_feed_down.py`: 5 minutes down (freshness warns, hour still
  counts) and 10 minutes down (freshness fails, the hour is a data outage), then recovery.
- **Meaning:** the MBTA feeds are unreachable. Ingest still runs, claims each minute and records
  every feed as `status = error`. The Lambda succeeds, so the Errors alarms stay quiet; the
  `headway-feed-errors` alarm fires after 5 minutes of errors
  ([alarm](#alarm-headway-feed-errors)). The data shows it too: `feed_gap_min` (warn at 5, fail at 10 minutes of an hour without
  fresh trip updates and vehicle positions), `agg_pipeline_health` error statuses, and the
  capture-completeness objective. An hour with fewer than 55 capture minutes is labelled
  `data_outage`, excluded from every metric and counted in `n_excluded_outage`.
- **First 3 commands:**
  ```sh
  aws logs tail /aws/lambda/headway-ingest-rt --since 15m --filter-pattern '?error ?CHAOS'
  curl -s -o /dev/null -w '%{http_code}\n' https://cdn.mbta.com/realtime/VehiclePositions.pb
  aws s3 cp "s3://$PUBLIC/public/boston/live/vehicles_latest.json" - | head -c 200
  ```
- **Likely causes:** the MBTA API or the network is down, the URL or key changed, or a live drill
  is running ([start and stop](#live-drills)). Compare with [SLO.md](SLO.md) before calling it an
  incident.
- **Fix:** none on our side if the feed is down; if the URL changed, update
  `config/cities/boston.yaml` and redeploy. If a drill is running, stop it.
- **Recovery:** the ingest summary lines show `statuses` all `ok` again; `feed_gap_min` of the
  next hour is 0; the outage hours stay labelled `data_outage` (history is not rewritten);
  capture completeness returns to 100% for new minutes.

### Simulation: missed invocations

- **Replay:** `tests/simulations/test_missed_runs.py`: three scheduled minutes never invoked.
- **Meaning:** the scheduler (or the function) skipped minutes. They leave no manifest. Three
  missed runs in an hour make it a `data_outage` hour even though the capture window is
  complete (57 of 60 minutes), and each missed minute spends capture-completeness budget (3 of
  180 minutes in the replay is 2.5 times the budget).
- **First 3 commands:**
  ```sh
  aws cloudwatch describe-alarms --alarm-names headway-ingest-rt-invocations-low --query 'MetricAlarms[].StateValue'
  aws scheduler get-schedule --name headway-ingest-rt-every-minute --query State
  aws s3 ls "s3://$RAW/manifests/boston/dt=$(date -u +%F)/hour=$(date -u +%H)/" | wc -l
  ```
- **Likely causes:** see [headway-ingest-rt-invocations-low](#alarm-headway-ingest-rt-invocations-low).
- **Fix:** restore the schedule; lost minutes cannot be recaptured (the feed is live only).
- **Recovery:** every new minute has a manifest; `missed_runs` is 0 for the next hour; the
  objective recovers as clean minutes dilute the day (see the error budget in
  [SLO.md](SLO.md#error-budget)).

### Simulation: duplicate delivery

- **Replay:** `tests/simulations/test_duplicate_delivery.py`: one minute delivered twice.
- **Meaning:** EventBridge delivers at least once, so a minute can reach ingest twice. The first
  delivery claims the minute with a conditional create and owns its manifest; the second writes a
  duplicate record under `manifests/boston/duplicates/` and does nothing else. Nothing is counted
  twice. One duplicate warns (`duplicate_deliveries`, warn 1, fail 5 per hour); it is not an outage.
- **First 3 commands:**
  ```sh
  aws s3 ls "s3://$RAW/manifests/boston/duplicates/dt=$(date -u +%F)/" --recursive | tail -5
  aws logs tail /aws/lambda/headway-ingest-rt --since 1h --filter-pattern 'duplicate delivery'
  aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Invocations --dimensions Name=FunctionName,Value=headway-ingest-rt --start-time $(date -u -v-1H +%FT%TZ) --end-time $(date -u +%FT%TZ) --period 3600 --statistics Sum
  ```
- **Likely causes:** normal at-least-once delivery; five or more in an hour suggests a scheduler
  or a retry setting problem ([Duplicate deliveries](INCIDENTS.md#duplicate-deliveries),
  [Lambda asynchronous retries](INCIDENTS.md#lambda-asynchronous-retries)).
- **Fix:** none for isolated duplicates. If many, check the schedules and function configs still
  have `maximum_retry_attempts = 0` (`tests/unit/test_scheduler_config.py` guards it in code).
- **Recovery:** `duplicate_deliveries` returns to 0 for the next hour; grain tests on
  `stg_manifests` and `int_captures` stay green.

### Simulation: invalid coordinates

- **Replay:** `tests/simulations/test_invalid_coordinates.py`: 50 vehicles with impossible
  positions in one capture (warn), then 250 over five captures (fail).
- **Meaning:** vehicles outside the service area are quarantined with their reason, never
  dropped silently, and the valid vehicles of the same capture are still used.
  `invalid_coordinate_share` warns at 0.5% and fails at 5% of the hour's vehicle rows. It is a
  data-quality signal, not an outage.
- **First 3 commands:**
  ```sh
  aws s3 ls "s3://$RAW/quarantine/boston/" --recursive | tail -5
  aws logs tail /aws/lambda/headway-curate --since 1h --filter-pattern 'invalid_coordinates'
  aws s3 cp "s3://$PUBLIC/public/boston/gold/agg_quality_checks_hourly.parquet" /tmp/quality.parquet && echo downloaded
  ```
- **Likely causes:** an upstream bug in the vehicle feed, a bounding-box change in
  `config/cities/boston.yaml`, or a parser change.
- **Fix:** if the feed is wrong, nothing; if the box is wrong, correct it and redeploy. Quarantined
  rows are kept for inspection and can be reprocessed from raw.
- **Recovery:** the share returns to under 0.5% in the next hour; the quarantine table stops
  growing for that reason.

### Simulation: GTFS static update

- **Replay:** `tests/simulations/test_static_update.py`: a second feed version, effective on the
  day it is loaded, next to the first.
- **Meaning:** the MBTA publishes a new schedule. Each service date joins exactly one feed
  version (the latest whose start date has passed), so a mid-day load switches the schedule per
  service date without doubling trip stops. `schedule_join_coverage` (warn under 90%, fail under
  70%) shows whether the realtime trips still match the schedule.
- **First 3 commands:**
  ```sh
  aws s3 ls "s3://$RAW/curated/boston/static/table=feed_info/"
  aws logs tail /aws/lambda/headway-static --since 26h
  aws s3 cp "s3://$PUBLIC/public/boston/gold/agg_quality_checks_hourly.parquet" /tmp/quality.parquet && echo downloaded
  ```
- **Likely causes:** a routine MBTA update (expected a few times a year); a coverage drop right
  after one means the new feed renumbered trips the realtime feed still reports the old way.
- **Fix:** none for a normal switch. A persistent drop under 90% is a matching problem to
  investigate; the previous version stays loaded and can still be read.
- **Recovery:** `schedule_join_coverage` is at or above 90% for the hours after the effective
  date; the previous service dates keep their old version in the history.

### Simulation: MotherDuck unavailable

- **Replay:** `tests/simulations/test_md_down.py`: three transform runs, the middle one with the
  `md_down` drill active.
- **Meaning:** the publish step fails, but gold is exported to S3 first, so the app (which reads
  S3) is unaffected and only MotherDuck consumers are stale. The run raises (the errors alarm
  fires), logs `publish_error`, is recorded as `failed` with `gold_exported_at` set and
  `gold_published` false, and counts as a bad slot for the transform-success objective. Gold
  freshness is not affected.
- **First 3 commands:**
  ```sh
  aws logs tail /aws/lambda/headway-transform --since 2h --filter-pattern '?publish_error ?"MotherDuck publish failed" ?CHAOS'
  aws s3 cp "s3://$PUBLIC/public/boston/gold/manifest.json" - | head -c 300
  aws ssm get-parameter --name /headway/chaos --query Parameter.Value --output text
  ```
- **Likely causes:** a MotherDuck outage, a rotated or expired token in
  `/headway/motherduck_token`, a DuckDB/extension version mismatch
  ([DuckDB version not supported by MotherDuck](INCIDENTS.md#duckdb-version-not-supported-by-motherduck),
  [MotherDuck extension directory was read-only](INCIDENTS.md#motherduck-extension-directory-was-read-only)),
  or a live drill (the last command shows an active switch).
- **Fix:** restore access or the token (`aws ssm put-parameter --name /headway/motherduck_token
  --type SecureString --overwrite --value ...` changes the account: confirm first). No replay is
  needed: the next run publishes everything again.
- **Recovery:** the next scheduled run logs no `publish_error`, `gold_published` is true, and
  `SHOW TABLES FROM headway;` in MotherDuck shows fresh tables; the failed slot stays in that
  day's transform-success numbers.

## Training job

The weekly forecast training (`headway-train`, only with `enable_r3`; ADR-019). Monday 06:00 UTC,
no retry. It reads the closed service days the transform keeps in `s3://<raw>/state/forecast_*`,
backtests, trains one model over the lines past the gate, and logs to MLflow. No alarm is attached
in this phase: look at it after the schedule.

**What a run returns** (the invocation result and the log line): `status` is `trained`,
`baselines_only` (no line passed the gate: `gate_reasons` says why per line, nothing under
`models/` changes), `no_data` (no partitions yet: the transform has not written closed days) or the
result is `{"skipped": "locked"}` (another run holds `mlflow/train.lock`).

**Run it by hand:** `aws lambda invoke --function-name headway-train --payload '{}' out.json`.

**Read the experiments:** `aws s3 cp s3://<raw>/mlflow/mlflow.db .` and, in the `ml/` environment,
`uv run mlflow ui --backend-store-uri sqlite:///$PWD/mlflow.db --default-artifact-root s3://<raw>/mlflow/artifacts`
Each run has the data window, the folds,
the git sha, `gate_reason.<line>` for every line left out, and the pooled metrics.

**The model that is live:** `aws s3 cp s3://<raw>/models/forecast/latest.json -`. To go back to an
earlier version, write `latest.json` again with that `version` and `path`
(`models/forecast/v<N>/`); version directories are never deleted by the job.

**Failure modes**

| Symptom | Cause | What to do |
| --- | --- | --- |
| `status: no_data` | no `state/forecast_examples/metric_version=1/` objects | check the transform logs for `forecast state write failed`; backfill with `rebuild_forecast_examples` |
| `skipped: locked` for more than 20 minutes | a run died holding `mlflow/train.lock` | the lock is replaced after 20 minutes; or delete the object |
| timeout or out-of-memory | more rows than sized for (memory 4096 MB, 300 s: sized on 448 k examples, 43 s, 2.3 GiB peak) | lower `TRAIN_MAX_ROWS_PER_DAY` or raise `train_memory_mb` / `train_timeout_s` in `infra/main/train.tf` |
| the run failed after the lock was taken | the database is uploaded anyway (with the half-finished run) | read the log, fix, run again |

## Incident summaries (LLM)

The transform writes `agg_incidents.summary_text` after the build (Release 3; ADR-008). It uses a
language model when it has a key and the daily cap allows, and the deterministic template otherwise.
Nothing here pages anyone; look at it when the Incidents page shows many "template used" badges.

**The key.** SSM SecureString `/headway/openai_api_key`, created by hand (it is not in Terraform):
`aws ssm put-parameter --name /headway/openai_api_key --type SecureString --value '<key>' [--overwrite]`.
Delete it (`aws ssm delete-parameter --name /headway/openai_api_key`) to switch the model off: the
next run uses the template, nothing else changes. Rotate by `put-parameter --overwrite`; a warm
container picks it up on its next run (the key is read on every run).

**The cap.** `HEADWAY_INCIDENT_LLM_DAILY_CAP` model calls per UTC day (default 50), counted across
runs from `state/incident_summaries/`. An exhausted cap shows as `rejection_reason =
daily_cap_reached` on the template summaries and `incident_llm_capped` in the run summary.

**The run summary** (transform log, `incident_*`): `incident_summaries_generated`, `_llm`,
`_template`, `incident_llm_calls`, `incident_llm_rejections`, `incident_llm_errors`,
`incident_llm_capped`. A summary exists once per incident version; an open incident is refreshed
every 3 hours.

**Rejections.** A model text that fails the number or causal check is dropped and the template is
used (`number_check = rejected`, `rejection_reason` names the number or phrase). A few are normal.
To read them: `aws s3 cp s3://<raw>/state/incident_summaries/dt=<day>/rows.parquet .` and filter
`number_check = 'rejected'`. A sudden run of rejections with a plausible text usually means the
evidence format changed (the dbt unit test `incident_evidence_json` and
`tests/unit/test_incident_evidence_shape.py` pin it) or the check is too strict for a new wording:
change the check or the instruction, never the stored text.

| Symptom | Cause | What to do |
| --- | --- | --- |
| every summary says `no_api_key` | the parameter is absent, misnamed, or the role lacks `ssm:GetParameter` | create it; check the `ReadOpenAiKey` statement in `infra/main/pipeline.tf` |
| `llm_error: LlmError` on many rows | provider outage, key revoked, quota | the template is in use meanwhile; fix the key or wait |
| `incident_summary_status: failed` | the step raised (logged: "incident summaries failed") | read the log; the export and publish continue with empty summary columns |
| cost higher than expected | the cap is per day, not per hour | lower `HEADWAY_INCIDENT_LLM_DAILY_CAP` |

## Analyst evals

`docs/EVALS.md` is the last real-model run of the analyst on the 40 questions of
`evals/questions.yaml`, over the frozen snapshot `evals/snapshot/` (the demo-r2 capture of
2026-11-22/23; rebuild it only on purpose with `uv run python -m evals.build_snapshot`, which
changes every gold answer). Nothing here touches AWS and nothing runs in CI except the scripted
`--fake` mode that tests the scorer.

**Run it:** `OPENAI_API_KEY=sk-... make evals`. It writes `docs/EVALS.md`, prints the cost, and
exits 1 when a threshold fails (numeric correctness >= 90%, unsupported claims <= 2%, refusal
correctness 100% on insufficient data and injection, p90 latency <= 10 s) or when the cost guard
cut the run short, 2 without a key. Commit the report only after a full real run.

**Cost:** about 2 model turns per question, roughly 1,000 to 2,000 tokens each with the five tool
definitions: a full run of `gpt-4o-mini` (the default; `--model` or `HEADWAY_ANALYST_LLM_MODEL`
change it) costs a few cents. `--max-cost` (default 0.50 USD) stops before a question that would
cross it and marks the run incomplete; `--max-questions N` and `--category C` run a part (a partial
report says so). A model without a price in `evals/run.py` reports "cost unknown": bound it with
`--max-questions`.

**Reading a failure:** the report lists each failed question verbatim with the gold values, the
status, the shown text, the model's draft, the checker's problems and the tool results (rows and
SQL). `could_not_verify` with a plausible draft means the check blocked it: fix the instruction or
the check (ADR-008), never the gold. A right number that is blocked usually means a missing sign: write a negative value as `-86 s`
or `86 s early`; `86 s` alone reads as positive (ADR-008, change log).

`make evals EVALS_ARGS="--fake --inject-faults"` must fail and name three questions (a wrong
number, an invented number, a wrong direction); if it does not, the scorer is broken.

## Alert classifier pilot

A pilot (`docs/ALERT_CLASSIFIER.md`, once generated); nothing in production depends on it.

**Label:** `uv run python scripts/export_alerts_for_labelling.py --local .local/replay` (or
`--s3-bucket <raw bucket>`, read-only) writes about 50 distinct alerts to `evals/alert_labels.csv`
with an empty `label` column and refuses to overwrite a file that already has labels (`--force`
overrides). Fill `label` by hand with one of the six classes in `evals/ALERT_LABELLING.md`.

**Report:** `make alert-classifier` (runs in `ml/`, needs no AWS) compares the rule baseline and TF-IDF
with logistic regression on the same seeded stratified 5-fold split and writes
`docs/ALERT_CLASSIFIER.md`. It exits 1 and names the rows while any label is empty or unknown.
With `OPENAI_API_KEY` set it also runs the LLM zero-shot model (about 50 short calls, a fraction of
a cent with the default `gpt-4o-mini`; `HEADWAY_ALERT_LLM_MODEL` changes it); without a key the
report lists it as not run. Fifty rows is a small sample: read differences of a few points as noise.

## Weather fetch

With `enable_r3` the daily static Lambda also refetches the last 21 days of NOAA daily weather into
`curated/<city>/table=weather_daily/` (ADR-022). A failure never fails the GTFS load: look for
`"weather": {"status": "error", ...}` in the `headway-static` log line. The comparison keeps its
earlier days and the weather table simply stops growing until the next successful run; the NCEI
service has no SLA and trails real time by about a week.

## Live drills

Two failures can be caused on purpose in the deployed pipeline with the chaos switch
([ADR-017](ARCHITECTURE_DECISIONS.md#adr-017-chaos-switch-for-failure-drills)): `feed_down`
(ingest) and `md_down` (transform). The other four simulations are replay-only.

The switch is the SSM parameter `/headway/chaos`. Terraform does not create it; the first
`put-parameter` does. It holds `{"mode": "feed_down" | "md_down" | "off", "until": "<UTC ISO>"}`
and stops by itself at `until`. A drill is real: hours become `data_outage`, objective budgets are
spent and alarms notify. Announce it, and record it in [INCIDENTS.md](INCIDENTS.md#drills).

**Start `feed_down` for 15 minutes** (macOS `date`; on Linux use `date -u -d '+15 minutes'`):

```sh
UNTIL=$(date -u -v+15M +%Y-%m-%dT%H:%M:%SZ)
aws ssm put-parameter --name /headway/chaos --type String --overwrite \
  --value "{\"mode\":\"feed_down\",\"until\":\"$UNTIL\"}"
```

**Start `md_down` for 15 minutes** (the hourly run is at `:20`, so start between `:06` and `:19`
and the window covers it):

```sh
UNTIL=$(date -u -v+15M +%Y-%m-%dT%H:%M:%SZ)
aws ssm put-parameter --name /headway/chaos --type String --overwrite \
  --value "{\"mode\":\"md_down\",\"until\":\"$UNTIL\"}"
```

**Stop early** (otherwise it ends at `until`):

```sh
aws ssm put-parameter --name /headway/chaos --type String --overwrite \
  --value "{\"mode\":\"off\",\"until\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
```

**Check it is off** (or never set): a missing parameter, an `off` mode and a past `until` all
mean no drill.

```sh
aws ssm get-parameter --name /headway/chaos --query Parameter.Value --output text
aws logs tail /aws/lambda/headway-ingest-rt --since 3m --filter-pattern CHAOS
aws logs tail /aws/lambda/headway-transform --since 70m --filter-pattern CHAOS
```

While a drill is active every invocation logs `CHAOS ACTIVE mode=... until=...`.

What to expect:

| Drill | You should see | Not expected |
| --- | --- | --- |
| `feed_down` 15 min | ingest summary lines with `statuses` all `error`; `feed_gap_min` fail for the hour; the hour labelled `data_outage`; capture completeness spends about 15 minutes of budget; the `headway-feed-errors` alarm after about 5 minutes (5 of 10 minutes with errors) | a Lambda `Errors` alarm (the function does not fail) |
| `md_down` over a `:20` run | one transform run with `publish_error` and `ChaosError`; `headway-transform-errors` alarm; gold `exported_at` still advances; that slot bad for transform success | gold freshness failing |

After the window: confirm recovery with the section of the drill
([Feed down](#simulation-feed-down), [MotherDuck unavailable](#simulation-motherduck-unavailable)),
and add a drill entry to [INCIDENTS.md](INCIDENTS.md#drills).
