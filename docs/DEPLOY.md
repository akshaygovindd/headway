# Deploying the stack

A walkthrough from a fresh clone to a running pipeline in AWS account region `us-east-1`. It
creates the buckets, the ingest, curate, static and transform Lambdas (the last three as container
images), their schedules, alarms and a cost budget. Run every command from the repository root
unless a `cd` is shown. Fill in the [walkthrough record](#walkthrough-record) as you go.

## Prerequisites

- AWS credentials for the target account in your shell (`aws sts get-caller-identity` works).
- `terraform` >= 1.10, `uv`, `make`, `docker` (running) with buildx.
- A MotherDuck account and token.
- Account note: the ingest Lambda sets no reserved concurrency (it was written for an account at
  the default limit of 10, which cannot reserve any). The account limit is now 1000, so only the
  API function reserves concurrency (R4). Runs take seconds once a minute and the scheduler never
  retries, so overlapping runs are unlikely.

```sh
git clone https://github.com/akshaygovindd/headway.git && cd headway
uv sync
```

## 1. Bootstrap the state bucket (once, local state)

```sh
cd infra/bootstrap
terraform init
terraform apply
terraform output -raw tfstate_bucket     # note this name
cd ../..
```

The bootstrap state file stays local in `infra/bootstrap/` (gitignored). Keep it.

## 2. Configure the backend and variables

```sh
cp infra/main/backend.hcl.example infra/main/backend.hcl
cp infra/main/terraform.tfvars.example infra/main/terraform.tfvars
```

- `infra/main/backend.hcl`: replace `<account_id>` so `bucket` equals the bootstrap output.
- `infra/main/terraform.tfvars`: set `alert_email` and `budget_email`. Leave the two image URI
  lines as they are; `make deploy` rewrites their tag.

Both files are gitignored. A fresh clone on another machine does not have them: copy them from the
machine that ran the bootstrap (the bootstrap state is local to that machine too), or redo step 1.

## 3. Store the MotherDuck token

```sh
aws ssm put-parameter --name /headway/motherduck_token --type SecureString \
  --value '<YOUR_MOTHERDUCK_TOKEN>'
```

Terraform only grants the transform Lambda read access to this parameter.

## 4. Build the ingest zip and create the ECR repositories

The ECR repositories belong to the same stack as the Lambdas that need their images, so create
them first with placeholder image URIs:

```sh
make lambda-zip                          # writes build/ingest.zip (arm64, Python 3.12)
cd infra/main
terraform init -backend-config=backend.hcl
terraform apply -target=aws_ecr_repository.pipeline -target=aws_ecr_lifecycle_policy.pipeline \
  -var curate_image_uri=placeholder -var transform_image_uri=placeholder
cd ../..
```

## 5. Deploy: `make deploy`

```sh
make deploy
```

Builds both images, logs in to ECR, pushes them tagged with the git short sha, sets
`curate_image_uri` and `transform_image_uri` in `terraform.tfvars` to that sha, runs
`terraform plan -out=tfplan` and applies it only if the plan shows `0 to destroy` (otherwise it
stops before the apply). On a fresh clone of an existing deployment the plan is not empty: the new
git sha changes the image tags, so expect a handful of in-place changes (for example
`0 to add, 5 to change, 0 to destroy`) and, once, new environment variables that earlier
deployments lacked. To redeploy new code later, run `make lambda-zip` (if ingest changed)
and `make deploy` again.

## 6. Seed the pipeline: `make seed`

```sh
make seed
```

Invokes, in order, `headway-static` (curate fails until a static GTFS version is loaded),
`headway-curate` with `{"task":"backfill_manifests"}`, `headway-curate` and `headway-transform`,
prints each result and stops at the first function error. After that the schedules take over:
ingest every minute, curate every 15 minutes, compaction at :05, transform at :20, static daily
at 09:00 UTC (04:00 EST, 05:00 EDT).

## 7. One-time manual steps

- Confirm the SNS email subscription: click the link in the "AWS Notification - Subscription
  Confirmation" email sent to `alert_email`. Alarm mails do not arrive until you do.
- Activate the `project` cost allocation tag (Billing and Cost Management, Cost allocation tags,
  user-defined tags). The budget filters on `user:project$headway` and sees no spend until the
  tag is active (activation can take up to 24 hours). Cost Explorer must be enabled for
  `cost_daily` to be written.

## 8. Verify

Wait two or three minutes after `make deploy`, then:

```sh
RAW=$(terraform -chdir=infra/main output -raw raw_bucket)
PUBLIC=$(terraform -chdir=infra/main output -raw public_bucket)
aws s3 ls "s3://$RAW/manifests/boston/" --recursive | tail -5
aws s3 ls "s3://$RAW/raw/boston/" --recursive | tail -6
aws s3 ls "s3://$RAW/curated/boston/" --recursive | tail -5
aws s3 ls "s3://$PUBLIC/public/boston/gold/"

URL=$(terraform -chdir=infra/main output -raw public_snapshot_url)
curl -s -D - "$URL" -o /dev/null | grep -i -E "^(HTTP|content-type|cache-control)"
curl -s "$URL" | head -c 600

aws logs tail /aws/lambda/headway-ingest-rt --since 10m
aws logs tail /aws/lambda/headway-curate --since 30m
aws logs tail /aws/lambda/headway-transform --since 2h
```

Expected:

- a new manifest roughly every minute and six raw objects per run (three feeds, then three more
  vehicle polls at +15, +30 and +45 s; fewer when a late run skips polls);
- a live snapshot with `vehicle_count` greater than zero, `Content-Type: application/json` and
  `Cache-Control: max-age=30`;
- a gold folder with the gold parquet files plus `manifest.json` (11 objects with Release 2 on; fewer
  with the defaults, and more with the R3 and R4 tables), and `cost_daily.parquet` after the
  first transform run at or after 06:00 UTC;
- one JSON summary line per run in the logs. The transform line reports `dbt_status`,
  `tests_failed`, `rows_exported`, `cost_rows` and `step_ms` (`s3_load`, `dbt_build`, `dbt_test`,
  `export_gold`, `cost`, `publish`). dbt test failures do not stop the export; they are counted, and `failed_tests` and `warned_tests` name them (DATA_QUALITY.md).

Transform runs are serialized with a lock object (`state/boston/transform.lock` in the raw bucket).
A run that finds a fresh lock, for example a manual invoke during the scheduled `:20` run, logs
`{"skipped": "locked"}` and exits successfully without touching anything. A lock older than 15
minutes is left by a crashed or timed-out run and is replaced by the next run.

MotherDuck: in the MotherDuck UI or CLI, `SHOW TABLES FROM headway;` lists the gold tables (the
`headway` database is created by the first publish).

Point the app at the published data (it also defaults to the public bucket of the reference
deployment):

```sh
echo "HEADWAY_DATA_URL=$(terraform -chdir=infra/main output -raw gold_url | sed 's#/gold$##')"
```

`make acceptance` reports the Release 1 acceptance tests (below).

## Release 1 acceptance

`make acceptance` (`scripts/acceptance.py`) reports PASS, FAIL or MANUAL for each test, using the
repository, the public gold and read-only AWS.

| # | Test | Checked by |
| --- | --- | --- |
| A1 | Captures stored with UTC timestamps and run IDs | newest raw manifest |
| A2 | Pipeline ran unattended 72 h with missed runs < 1% of expected | public `agg_pipeline_health` |
| A3 | Scheduled and predicted values in separate columns; prediction snapshots per collection time | `fct_stop_predictions` schema and grain test |
| A4 | Duplicate, missing and invalid records measured | seeded faults in replay; pipeline health |
| A5 | dbt models documented and tested | `scripts/check_dbt_docs.py` |
| A6 | Coverage and freshness beside every metric | metric card policy test |
| A7 | Live map reads the cached snapshot only | no MBTA URL in `app/` |
| A8 | Reproducible deployment | fresh-clone walkthrough below (MANUAL) |
| A9 | End-to-end test runs locally from samples without AWS | `make e2e` |
| A10 | `ARCHITECTURE_DECISIONS.md` and `METRICS.md` written | docs checks |
| A11 | Lineage diagram in `docs/LINEAGE.md` | docs check |
| A12 | Cost measured from real billing | `cost_daily` rows |

## Walkthrough record

Fill this in on a fresh clone (acceptance test A8). Times are wall clock, including waiting.

| Step | Time taken | Notes |
| --- | --- | --- |
| Prerequisites and clone | 1 s | `git clone` + `uv sync`, uv cache warm; docker running |
| 1. Bootstrap state bucket | not re-run | `terraform apply` is not run on an existing account; state bucket already exists, `backend.hcl` copied from the original checkout |
| 2. Backend and tfvars | under 1 s | copied the two local files; image tags set to the clone's sha `cbf8d6c` |
| 3. MotherDuck token (SSM) | 0 s | not re-run; `aws ssm get-parameter` confirms the SecureString exists |
| 4. Ingest zip and ECR repositories | 1 s (zip), 6 s (`terraform init`) | zip is 4.4 MB; the ECR `apply -target` is not re-run (repositories exist) |
| 5. `make deploy` | 18 s image build (layer cache warm; a cold build takes longer), 12 s `terraform plan` | plan `0 to add, 5 to change, 0 to destroy` (image tags and one new env var); images built, not pushed. Push/apply: verified by the production deploy of 25 Nov (memory fix, commit de9d2a8) |
| 6. `make seed` | not re-run | invokes Lambdas; verified by the original deployment |
| 7. Manual steps (SNS, cost tag) | not re-run | account settings, done once |
| 8. Verify | 7 s | read-only commands: manifests current, snapshot 200 with `Cache-Control: max-age=30`, 11 gold objects, transform log line present |
| **Total** | about 45 s of commands | walkthrough of 26 Nov 2026 from a fresh clone, stopping at `terraform plan` |

## Operations

`scripts/bench_transform.py` (`uv run python scripts/bench_transform.py --days 1 --backfill`)
times the transform's dbt build and test against replicated manifests; it is a development tool,
not part of the deployment.

### Backfill the curated manifests tables (once, when upgrading an existing deployment)

The transform reads run manifests and quarantine records from curated Parquet
(`curated/boston/table=manifests/` and `table=quarantine/`), which curation writes from the new
image onward. Build them for the raw data that already exists by invoking the curate Lambda once,
right after the apply that deploys the new images and before the next `:20` transform run (a
transform that runs earlier sees only the manifests curated since the deploy):

```sh
aws lambda invoke --function-name headway-curate --cli-read-timeout 330 \
  --cli-binary-format raw-in-base64-out --payload '{"task":"backfill_manifests"}' /dev/stdout
```

It reads every raw manifest and quarantine object, so it takes about a minute per week of data.
The raw JSON stays the source of truth and the task is idempotent: re-running it, or running it
while curation is writing, never duplicates rows (a fresh deployment does not need it). The
summary line reports `manifest_rows`, `quarantine_rows` and `hours_rewritten`; a second run
reports `hours_rewritten: 0`.

## Tear down

```sh
cd infra/main && terraform destroy
```

Both data buckets must be emptied first (`aws s3 rm s3://<bucket> --recursive`). The bootstrap
bucket is versioned; remove it manually if you really want to delete state history.

## Release 2 switches (both off by default)

Deploying Phase 2.1 before Release 1 sign-off must not change behaviour, so two Terraform variables
gate it. With the defaults (`vehicle_polls = 1`, `enable_r2 = false`) the ingest Lambda takes one
poll per minute as before and the transform Lambda skips every dbt model and test tagged `r2`.
Still different after a deploy: manifests gain `poll_index` (always 0), `agg_pipeline_health` gains
two columns, the static Lambda also writes the two geometry tables, and the ingest Lambda claims
a minute before fetching (a second delivery fetches nothing). The ingest function is 256 MB with
a 58 s timeout (it read 114 MB, 89 %, at 128 MB), which fits both settings.

Switch-on, Phase 2.6, in this order:

1. Static geometry: invoke `headway-static` once. The latest feed version is already loaded, so it
   only writes `shape_points_dist` and `stop_shape_positions` for it (check both exist under
   `curated/boston/static/`).
   The daily 09:00 UTC run also writes them, so first check
   `aws s3 ls s3://<raw>/curated/boston/static/ | grep -E 'shape_points_dist|stop_shape_positions'`
   and invoke only if either is missing.
2. Set `enable_r2 = true` in `terraform.tfvars`, `make deploy`, and wait for one transform run:
   `fct_reconstructed_arrivals`, `agg_arrival_reconstruction_daily` and the Release 2 metric models
   (accuracy, actual deviation, headway adherence, ETT, active alerts) exist, the gold export gains
   those six aggregates (and only then; the Accuracy page and the Line explorer headway view appear
   when the files do), and its logged step times are in line with the earlier runs. Run this on
   1-minute data first.
3. Set `vehicle_polls = 4`, `make deploy`. Check the next manifests: six entries, `poll_index`
   0-3, a run of 45-58 s, `Max Memory Used` in the Lambda REPORT line well under the 256 MB limit
   (127 MB, 50 %, was measured on single-poll runs; above about 190 MB, set `memory_size` to 512 in
   `infra/main/lambda.tf` and deploy again), the live snapshot updating every 15 s.
4. Turning either back off is the same variable change; data already written stays valid.

## Release 3 switch (off by default)

`enable_r3 = true` (it requires `enable_r2 = true`, checked by a Terraform validation) sets
`HEADWAY_ENABLE_R3=true` on the transform Lambda, which then also builds the dbt models tagged
`r3`: the forecast examples and the training gate (`docs/METRICS.md`, ADR-019). They are internal:
they are not exported or published. With the default the Lambda skips every `r3` model and test;
local and CI builds always build them. Check the logged step times after switching it on, as for R2.
The same switch sets `HEADWAY_ENABLE_R3` on the daily static Lambda, which then also fetches the
NOAA daily weather (ADR-022, `docs/RUNBOOK.md`), and grants it write access to
`curated/<city>/table=weather_daily/`. `agg_weather_comparison` is exported with the R3 gold tables.

Switch-on, Phase 3.2 (nothing is deployed by the repository's tests; do this deliberately):

1. Create the `headway-train` ECR repository: it is part of the Terraform with `enable_r3`, so
   either apply the repository alone first
   (`terraform apply -target='aws_ecr_repository.pipeline["train"]'` with `enable_r3 = true`), or
   create it by hand with the same name.
2. `make images-push` builds and pushes all three images and prints `train_image_uri`; put it in
   `terraform.tfvars` next to `enable_r2 = true` and `enable_r3 = true`. `make deploy` plans and
   applies (the plan must destroy nothing). This adds the `headway-train` function, its log group,
   role, the weekly schedule `headway-train-weekly` (Monday 06:00 UTC) and lets the transform role
   write `state/forecast_*`.
3. Wait for transform runs, then check that closed days appear:
   `aws s3 ls s3://<raw>/state/forecast_examples/metric_version=1/`. Only service days that have
   closed since the switch are written; to backfill earlier days still inside the lookback invoke
   the transform with `{"task":"rebuild_forecast_examples","from":"YYYY-MM-DD","to":"YYYY-MM-DD","metric_version":1}`
   (at most 14 days per call).
4. First run by hand: `aws lambda invoke --function-name headway-train --payload '{}' out.json`.
   Until a line has 21 usable days (3 of them weekend days) the result is `baselines_only`, with
   the reason per line; there is no `models/` object and no `latest.json`. That is the expected
   outcome for the first weeks.
5. Turning it off is `enable_r3 = false`: the function and schedule are destroyed (the data under
   `state/forecast_*`, `mlflow/` and `models/` stays).

Switch-on, Phase 3.3 (forecast serving and the Forecast page; built and smoke-tested, not deployed):

1. `make images-push` also rebuilds the transform image, which now carries LightGBM (libgomp) and
   the serving modules of `headway_ml`: it is larger than before, so check its size and the cold
   start in the first logs. `make deploy` applies the transform role's read access to
   `models/forecast/*` (behind `enable_r3`; the plan must destroy nothing).
2. Nothing else to configure: with `enable_r3` each transform run loads the training job's reports,
   scores the next hour and exports `agg_forecast_next_hour` and `agg_forecast_gate_status`
   (`agg_forecast_backtest` only once a line has passed the gate). Check the run summary for
   `forecast_serving_status` (`baselines_only` until a model exists), `forecast_model_rows`,
   `forecast_reports` and the `forecast_reports` and `forecast_serving` step times; a failure
   there is logged and never fails the run.
3. Check the export: `aws s3 ls s3://<public>/public/boston/gold/ | grep forecast` lists the two
   tables; the Forecast page appears in the app once they exist. Until a line has 21 service days
   with 3 weekend days it shows the gate status and baselines only.
4. Rolling back a model is writing `models/forecast/latest.json` for an earlier version (see
   ADR-019); the next transform run serves it. Turning serving off is `enable_r3 = false`.
5. The training job now keeps the whole test weeks in memory (only the rows a model is fitted on
   are capped, 8000 per day by `TRAIN_MAX_ROWS_PER_DAY`, stratified by line and horizon): after the
   first runs on real data check `Max Memory Used` and the duration of `headway-train` against
   `train_memory_mb` and `train_timeout_s` in `infra/main/train.tf` and raise them if needed.

Switch-on, Phase 3.4 (incidents, summaries, alert vs reality; built and smoke-tested, not deployed):

1. `make images-push` and `make deploy`: the transform role may now read
   `/headway/openai_api_key` and write `state/incident_summaries/` (behind `enable_r3`; the plan
   must destroy nothing). Nothing is created for the key.
2. **Create the key by hand (optional)**, as a SecureString under the account's default key:
   `aws ssm put-parameter --name /headway/openai_api_key --type SecureString --value '<key>'`.
   Without it every summary is the deterministic template, which is a complete, supported mode.
   The model name is `HEADWAY_INCIDENT_LLM_MODEL` (default `gpt-4o-mini`) and the daily call cap
   `HEADWAY_INCIDENT_LLM_DAILY_CAP` (default 50); set them on the transform Lambda's environment
   if you change them.
3. The first deployed history of `agg_actual_deviation_hourly` starts when the switch goes on: the
   transform writes each closed day of it once (`history_written` in the run summary,
   `s3://<public>/public/boston/history/table=agg_actual_deviation_hourly/`). The deviation rule
   of incidents needs 14 closed days of the same day type (7 weeks for weekends) and stays off, and
   says so in each incident's evidence, until then. To start earlier, backfill with
   `{"task":"rebuild_history","from":...,"to":...,"metric_version":3}` (at most 14 days per call),
   as far back as the raw curated data goes.
4. Check the run summary for `incident_summary_status` (`ok`), `incident_count`,
   `incident_summaries_llm` / `incident_summaries_template`, `incident_llm_calls` and
   `incident_llm_rejections`; the export lists `agg_incidents` and `agg_alert_matching_daily`, and
   the Incidents page appears in the app. A failure of the step is logged and never fails the run.
5. Once a few weeks of incidents exist, do the alert hand check: `docs/MEASUREMENTS.md`.

### AI analyst (Phase 3.5; nothing is deployed by the repository's tests)

1. The transform image now carries `headway_mcp`; rebuild and push it (`make images-build`,
   `make images-push`) and deploy. With R3 on, each run writes `gold/analyst_examples.json`
   (`analyst_examples` in the run summary; a failure is logged and never fails the run).
2. Live questions are off until the Streamlit app has the secret. In the app's secrets
   (Streamlit Community Cloud: Settings, Secrets) add `OPENAI_API_KEY = "sk-..."`. Optional
   environment variables: `HEADWAY_ANALYST_DAILY_BUDGET` (default 100),
   `HEADWAY_ANALYST_SESSION_LIMIT` (default 10), `HEADWAY_ANALYST_LLM_MODEL` (default gpt-4o-mini).
   Without the secret the page shows the examples only.
3. Check: open the AI analyst page, ask one question, and confirm the "verified" badge, the SQL
   and the freshness line. The audit lines (`headway_mcp.audit`) are in the app log.

## Release 2 history, run state and SLOs

With `enable_r2 = true` the transform Lambda also keeps two kinds of object, both written by its
role (`terraform validate` is the only check made before a deploy; the policy is in
`infra/main/pipeline.tf`):

- **Run state** at `s3://<raw>/state/transform_runs/dt=YYYY-MM-DD/rows.parquet`: one row per
  invocation, loaded at the start of the next run. It is written whether or not Release 2 is on.
  Losing it only empties the SLO tables for the missing days.
- **History** at `s3://<public>/public/boston/history/table=<agg>/metric_version=<N>/service_date=YYYY-MM-DD/part.parquet`:
  one object per closed service day and daily aggregate, publicly readable like the gold. A day
  appears after 06:00 local on the next day. Objects are created once (conditional put) and never
  replaced; the role can create but not delete them. MotherDuck gets `history_<agg>` tables.

Check after the first full day: the history prefix has one object per table for the closed day,
`agg_slo_daily` has its four objectives, and `make slo-report` writes a report instead of the
"not enabled" notice.

Backfill history for explicit days (for example after bumping a metric version in
`dbt/headway_transform/history.py`; at most 14 days per call, `metric_version` must equal the
family's current version; existing days are left as they are and gold is not touched):

```
aws lambda invoke --function-name headway-transform --cli-read-timeout 930 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"task":"rebuild_history","from":"2026-11-16","to":"2026-11-22","metric_version":3}' out.json
```

A backfill reads as many days of curated data as separate it from today, inside the 900 s limit of
the function; split a long range into calls of a few days if one times out.

## Release 4 switch: public metric API (off by default)

Phase 4.5 adds four R4 gold tables (`agg_trip_time_budget`, `agg_slow_segments`,
`agg_slow_zone_comparison`, `agg_accessibility_outages`) and the endpoints `/v1/lines/{line}/trip-time-budget`,
`/v1/slow-zones` and `/v1/accessibility`. They ride on the same `enable_r4` switch: no new variable,
resource or permission, and no new feed. Measured cost with R4 on: about 15 s more per transform run and
no extra memory ([MEASUREMENTS](MEASUREMENTS.md#release-4-phase-45-tables-cost-of-the-four-new-models)).
The official slow-zone list ships empty and is filled by hand
([RUNBOOK](RUNBOOK.md#capturing-the-official-slow-zone-snapshot-by-hand)); until then `/slow-zones` says
"official list not captured yet".

Phase 4.1 is built and tested but not deployed. `enable_r4 = true` adds the API Lambda (container,
arm64), an HTTP API with throttling and an access log, an ECR repository `headway-api` and a 5xx
alarm (ADR-023, `docs/API.md`). It needs no new secrets and no access to any bucket.

**Prerequisite: the account's Lambda concurrency limit must be at least 100 plus the reservation.**
The API function reserves concurrency (`api_reserved_concurrency`, default 3) out of the account
pool, and Lambda keeps 100 unreserved. The account limit is 1000 (checked 2026-11-27), so this is
now a confirmation, not a blocker: Terraform cannot read the quota, so it requires you to state it
as `api_confirmed_concurrency_limit = 1000` (at least 103 by default). Without that line
`enable_r4 = true` fails validation. The error text in `variables.tf` still talks about new accounts
at 10; ignore it.

```sh
aws lambda get-account-settings --query 'AccountLimit.[ConcurrentExecutions,UnreservedConcurrentExecutions]'
```

If `ConcurrentExecutions` ever prints less than 103: Service Quotas > AWS Lambda > Concurrent
executions > request an increase to 1000, and wait for approval. Set the value the command
prints in `terraform.tfvars` as `api_confirmed_concurrency_limit`. Terraform cannot read the quota,
so the variable is your statement of it: `enable_r4 = true` fails validation while it is below
100 plus the reservation, and the Lambda has a matching precondition, so the step cannot be skipped silently.

1. Check the prerequisite above and set `api_confirmed_concurrency_limit`.
2. Create the repository: it is part of the Terraform with `enable_r4`
   (`terraform -chdir=infra/main apply -target='aws_ecr_repository.pipeline["api"]'` with
   `enable_r4 = true` and `api_image_uri` set to any non-empty placeholder), then
   `make images-push ENABLE_R4=true`, which prints `api_image_uri`.
3. In `terraform.tfvars` set `enable_r4 = true`, `api_image_uri`, `api_cors_origins` (the Vercel
   domain once it exists; the default is a placeholder) and optionally `api_reserved_concurrency`,
   `api_throttle_rate`, `api_throttle_burst`. `make deploy ENABLE_R4=true` plans and applies; the
   plan must destroy nothing.
4. First checks:
   ```sh
   API=$(terraform -chdir=infra/main output -raw api_url)
   curl -si "$API/v1/meta" | head -20          # 200, cache-control: public, max-age=300
   curl -s "$API/v1/lines/Red/reliability" | head -c 600
   curl -si "$API/v1/lines/Purple/reliability" | head -12   # 422 with the allowed lines
   curl -si -H 'Origin: https://headway.example.vercel.app' "$API/v1/slo" | grep -i access-control
   curl -s -o /dev/null -w '%{http_code}\n' "$API/docs"       # 200
   aws logs tail /aws/lambda/headway-api --since 5m           # one JSON access line per request
   aws lambda get-function-concurrency --function-name headway-api   # ReservedConcurrentExecutions 3
   ```
   The second request to a warm container should report `"served_from":"cache"` in `provenance`.
5. Turning it off is `enable_r4 = false`: the function, gateway and alarm are destroyed; nothing else
   depends on them.

**Site tables (the public site's data).** `enable_r4 = true` together with `enable_r2 = true` also
sets `HEADWAY_ENABLE_R4=true` on the transform Lambda (`tostring(var.enable_r4 && var.enable_r2)`;
false by default, so a plan with R4 off changes nothing). The transform then also builds the dbt
models tagged `r4` and exports `agg_headway_segment_window`, `agg_marey_last_hour`,
`agg_capture_heartbeat_hourly`, `agg_ett_band_window` and `agg_service_disruptions` with the gold tables;
the API serves them as `/v1/lines/{line}/segments`, `/marey`, `/trip-time`, `/disruptions` and
`/v1/pipeline/heartbeat` (404
`not_available` before). Switch-on: set `enable_r4 = true` next to `enable_r2 = true` and deploy
(`make deploy ENABLE_R4=true`); after the next transform run check that the export has the
tables (`curl -s "$API/v1/meta"` lists them in `tables_in_export`, and `/v1/lines/Red/segments`
answers 200) and read the logged step times as for R2. Turning them off is `enable_r4 = false`:
the next run builds and exports without them, and the API answers 404 for them as soon as the
manifest no longer lists the tables (old objects are not deleted).

## Public site on Vercel (not executed)

The site in `web/` is built and tested locally only. When the API is live (`enable_r4`):

1. Create a Vercel project from this repository with root directory `web` (framework: Next.js, Node 20 or newer).
2. Set `HEADWAY_API_URL` to `terraform output -raw api_url` (no trailing slash) and
   `NEXT_PUBLIC_SITE_URL` to the site's public origin (see the go-live checklist below). Pages are rendered on the
   server, so the API's `API_CORS_ORIGINS` does not need the site's origin unless a browser calls the API directly.
3. Pages revalidate every 300 s, matching the API's `Cache-Control: public, max-age=300`.
4. Check `/`, `/lines/Red` and `/pipeline` against the live API, then attach the domain.

See [web/README.md](../web/README.md).


## Sunday 2026-11-29 deploy runbook (dry run, 2026-11-27)

Nothing here has been executed: the plans below are `terraform plan` runs on copies of
`terraform.tfvars` (the real file and `tfplan` are untouched), plus read-only AWS calls.
Current state (checked 2026-11-27): account 381232271655, Lambda limit 1000, ECR has only
`headway-curate` and `headway-transform`, `enable_r2 = true`, `vehicle_polls = 4`, image tag
`:324934d`, facts and history exist at `metric_version=1` only, `/headway/openai_api_key` does not
exist. A plan of the current tree with the current tfvars shows no differences, so `make deploy`
changes only image tags.

**Data to rebuild.** Capture started 2026-11-22 (partial day). After 06:00 ET on Sunday the closed
service days are 2026-11-22 to 2026-11-28, seven days: one call per task, well inside the 14-day
cap. 22-25 Nov are 1-minute data (corrected), 26 Nov is mixed, 27-28 Nov are 15-second data
(uncorrected, ADR-026).

**Timing.** The transform runs at :20 every hour. Start right after a :20 run has finished (about
:30) so the rebuilds have about 50 minutes. A scheduled run that starts while a rebuild holds
`transform.lock` returns `{"skipped":"locked"}`; that is not an error. A rebuild started while a
scheduled run holds the lock is skipped the same way: re-invoke it.

### A. Deploy HEAD (R2 on, R3 and R4 off)

```sh
git status --short | grep -v mlruns     # clean
git log -1 --oneline
make deploy                              # builds curate+transform images, pushes, plan, applies if 0 destroy
```
Expected plan: `0 to add`, a handful `to change` (curate, static and transform image tags), `0 to destroy`.
Check: `aws lambda get-function --function-name headway-transform --query Code.ImageUri`
shows the new sha, and the next ingest manifests keep `poll_index` 0-3.
Risk: `make deploy` edits `terraform.tfvars` with `sed` and verifies only the curate and
transform tags. From here until step B finishes, **a scheduled transform run rebuilds every
window day from raw data** (facts at v3 do not exist yet). If it lands first it takes about as long
as a run with facts off. The duration alarm needs more than 600 s in two consecutive runs, so one
slow run does not page.
Rollback: `git checkout` the previous sha tag in `terraform.tfvars` (`:324934d`) and `make deploy`.

### B. `rebuild_facts` v3

```sh
aws lambda invoke --function-name headway-transform --cli-read-timeout 930 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"task":"rebuild_facts","from":"2026-11-22","to":"2026-11-28","metric_version":3}' \
  /dev/stdout
```
One invocation (7 days). Expected: `fct_reconstructed_arrivals` and `fct_headways` v3 written for
each closed day, `duration_ms` in the order of 200-600 s (measured floor for a full rebuild on a
laptop is 183 s; Lambda was slower, production runs took 453 s on 1-minute data before the
incremental facts). The limit is 900 s. If it times out, no data is lost (days are written once
and skipped when present): re-run with the same payload, or split newest day first, for example
`2026-11-26..2026-11-28` then `2026-11-22..2026-11-25`.
Check:
```sh
R=headway-raw-381232271655
aws s3 ls s3://$R/state/facts/fct_reconstructed_arrivals/metric_version=3/
aws s3 ls s3://$R/state/facts/fct_headways/metric_version=3/      # 7 service_date= prefixes each
```
Rollback: set both versions back in `facts_state.py` and deploy; v1 partitions are still there.

### C. `rebuild_history` v3

```sh
aws lambda invoke --function-name headway-transform --cli-read-timeout 930 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"task":"rebuild_history","from":"2026-11-22","to":"2026-11-28","metric_version":3}' \
  /dev/stdout
```
**One invocation** (7 days is under the 14-day cap) writes every family at version 3 (seven
families; `stop_deviation` stays at v1). It also publishes to MotherDuck, so the SSM token must
work. Expect about the same duration as B or a little less; the summary shows `history_written`
per table and `history_existing`.
Check: `aws s3 ls s3://headway-public-381232271655/public/boston/history/table=agg_actual_deviation_daily/metric_version=3/`
lists service dates 2026-11-22 to 2026-11-28; repeat for `agg_actual_deviation_hourly`. Then wait
for the next :20 run: `facts_loaded_days` lists the closed days, `duration_ms` is under 300000,
no `skipped`.
Risk: history objects are conditional puts that the transform role cannot delete. A wrong v3
object cannot be undone by rollback; it needs an admin delete and a re-run.

### D. Release 3 switch-on (`enable_r3 = true`)

Do this only after A to C are verified by one clean scheduled run. Plan (real image URIs
replaced by placeholders, tfvars copy): **8 to add, 5 to change, 0 to destroy.**

| Change | Resource |
| --- | --- |
| add | ECR repo `headway-train` and its lifecycle policy |
| add | Lambda `headway-train` (arm64 image, 4096 MB, 300 s timeout, 1024 MB ephemeral, no reserved concurrency), its log group (14 days), IAM role and role policy (own `state/forecast_*`, `mlflow/*`, `models/forecast/*` prefixes), event invoke config (0 retries) |
| add | EventBridge schedule `headway-train-weekly`, `cron(0 6 ? * MON *)` UTC (Monday 01:00 ET) |
| change | Transform Lambda and static Lambda: `HEADWAY_ENABLE_R3 = true` (static then fetches NOAA weather in its 09:00 UTC run) |
| change | IAM: transform role gains `state/forecast_*`, `state/incident_summaries/*` and `models/forecast/*`; static role writes `table=weather_daily/`; scheduler policy may invoke the train Lambda |
| none | No new alarm (a train failure alerts nobody), no new secret: `/headway/openai_api_key` read access already exists |

Steps:
```sh
# 1. create the repository (applies two ECR resources; set enable_r3 = true and a placeholder train_image_uri first)
terraform -chdir=infra/main apply -target='aws_ecr_repository.pipeline["train"]' \
  -target='aws_ecr_lifecycle_policy.pipeline["train"]'
# 2. build and push; copy the printed train_image_uri into terraform.tfvars next to enable_r3 = true
make images-push
# 3. deploy (the plan must show 0 to destroy)
make deploy
```
Checks: `aws lambda get-function --function-name headway-train`; after the next :20 run the run
summary has no `r3` errors and the step times are in line (R3 adds about 15 s on the laptop
measurement); `aws s3 ls s3://$R/state/forecast_examples/metric_version=1/` appears once the first
service day closes after the switch (Monday 06:00 local at the earliest).
Rollback: `enable_r3 = false` and `make deploy` (the function and schedule are destroyed; data
under `state/forecast_*`, `mlflow/`, `models/` stays).

**First runs.**
- *Transform:* builds the `r3` models and writes `forecast_examples` for closed days only, from
  the corrected v3 arrivals (R3 has never been on, so no v1 examples exist). Exports
  `agg_forecast_next_hour` and `agg_forecast_gate_status`; the Forecast page shows the gate
  status and baselines.
- *Train, Monday 06:00 UTC:* far below the 21 usable days (3 on weekends) the gate needs, so the result is
  `baselines_only`; no model, no `latest.json`. Sized on 448k examples, 43 s and 2.3 GiB peak,
  never run on real data: check `Max Memory Used` and the duration afterwards.
- *Static:* the next 09:00 UTC run refetches 21 days of NOAA daily weather.
- *LLM:* no key exists, so every incident summary is the deterministic template ($0). If you
  create the key later: model `gpt-4o-mini`, at most 50 calls per UTC day
  (`HEADWAY_INCIDENT_LLM_DAILY_CAP`, env only, not in Terraform), 20 s timeout: cents per day at
  most (an estimate, not measured). The AI analyst's own cap is separate and lives in Streamlit.
- *Budget:* $15 per month with alerts at $12 and $15 on the `project=headway` tag; R3 adds a
  weekly job and a few storage prefixes.

**Corrected arrivals, decide before the first service day closes.** `forecast_examples` v1 days
are written once and never replaced. Examples for 15-second days (27 Nov onwards) are therefore
frozen uncorrected, while 22-25 Nov are corrected: a bias in whatever trains on them. ADR-026's
refit trigger (three full 15-second days) is met on 30 Nov. Either accept that, or bump
`forecast_state.TABLES` after the refit and run `rebuild_forecast_examples` with the new version
(it rejects v3 today, and at v1 it skips existing days). Do not run a v1 backfill on Sunday.

### Sunday order, in one list

1. `make deploy` (A), check image and a clean ingest.
2. `rebuild_facts` 2026-11-22..2026-11-28 v3 (B), check the S3 listings.
3. `rebuild_history` same range v3 (C), check the history listing.
4. Watch the next :20 run: `duration_ms` under 300000, `facts_loaded_days` 7, memory under 85%
   (`TransformMemoryUsedPct`), no failed dbt tests (the `unique_grain_agg_pipeline_health_run_id` failure seen
   on synthetic data does not occur on real data).
5. R3 (D) only if step 4 is clean; otherwise stop. Nothing in D is needed for the site.

## Release 4 and Vercel go-live checklist (dry run, 2026-11-27)

**R4 plan** (tfvars copy with R3 and R4 on, `api_confirmed_concurrency_limit = 1000`, placeholder
image URIs and CORS origin): **21 to add, 5 to change, 0 to destroy.** The 13 additions beyond R3's 8 are:

| Resource | Setting |
| --- | --- |
| ECR repo `headway-api` and lifecycle policy | arm64 image |
| Lambda `headway-api` | 1024 MB, 15 s timeout, 512 MB ephemeral, **reserved concurrency 3**, env `HEADWAY_DATA_URL` (public bucket) and `API_CORS_ORIGINS` |
| IAM role and policy | logs only, no bucket access |
| Log groups | `/aws/lambda/headway-api` and the API access log, 14 days each |
| HTTP API, integration (20 s), `$default` route, `$default` stage (auto deploy) | **throttle 10 requests/s, burst 20** (`api_throttle_rate`, `api_throttle_burst`) |
| Lambda permission | API Gateway may invoke the function |
| Alarm `headway-api-5xx` | `AWS/ApiGateway` 5xx sum >= 5 in 5 minutes, to the alerts topic |

Changes: transform env `HEADWAY_ENABLE_R4=true` (needs R2), and the same IAM and scheduler
changes as R3. CORS is answered by the app, not the gateway; `api_cors_origins` is a comma list
without `*`.

**Concurrency guard.** The account limit is 1000. The check is `api_confirmed_concurrency_limit >=
100 + api_reserved_concurrency`; it is a statement, so set `api_confirmed_concurrency_limit =
1000` (without it the plan fails validation). Reserved 3 of 1000 leaves 997 unreserved, far above
Lambda's 100 floor, so ingest, curate and transform are not starved.

**Transform cost with R2 + R3 + R4** ([MEASUREMENTS](MEASUREMENTS.md#release-4-phase-45-tables-cost-of-the-four-new-models),
2026-11-26, laptop, 4x vehicle data, 3008 MB at 1.7 CPUs): +14.7 s on the first run (127.7 to
142.4 s), no measurable memory change (peak 79-80% of 3008 MB; the budget is 85%), nine more
tables exported (31 to 40). Production is slower than the laptop, so read the first real run's
step times. The R4 smoke run took 19 s at 21% memory.

**API smoke test against the image (run 2026-11-27, passed).** `make api-smoke` builds
`headway-api:local` for arm64 and checks `/v1/meta` (200, `cache-control: public, max-age=300`,
CORS header), a line route (200), an invalid line (422 with the allowed lines), `/docs` (200) and
that the baked `httpfs` extension loads. I also ran that image over the demo gold
(`.local/demo/public/boston` mounted read-only at `/fixture`): `/v1/meta` and
`/v1/lines/Red/reliability` return 200, and `/v1/slo` returns `access-control-allow-origin` for
an allowed origin and none for another. The image speaks the Lambda Runtime Interface
Emulator, not plain HTTP, so a local site needs a shim that turns each request into an API Gateway v2
event and POSTs it to `http://127.0.0.1:<port>/2015-03-31/functions/function/invocations`.
`make api-dev` (uvicorn over the same gold) is the plain-HTTP alternative.

**Go-live order (R4).**
1. `terraform.tfvars`: `enable_r4 = true`, `api_confirmed_concurrency_limit = 1000`, a placeholder
   `api_image_uri`, `api_cors_origins` (set the real Vercel domain later; see below).
2. `terraform -chdir=infra/main apply -target='aws_ecr_repository.pipeline["api"]'`, then
   `make images-push ENABLE_R4=true`, copy the printed `api_image_uri`.
3. `make deploy ENABLE_R4=true` (plan must show 0 to destroy).
4. Checks: the commands in the R4 section above (`/v1/meta`, 422 for Purple, CORS header, `/docs`,
   reserved concurrency 3, one JSON access line per request). Then wait one transform run and
   confirm `tables_in_export` lists the five site tables and `/v1/lines/Red/segments` answers 200.
5. Capture the official slow-zone snapshot by hand (RUNBOOK), or `/slow-zones` says the list is not captured.

### Vercel readiness (no account actions taken)

**Local production check (run 2026-11-27, passed).** API image over the demo gold on :8000, then
`HEADWAY_API_URL=http://localhost:8000 NEXT_PUBLIC_SITE_URL=http://localhost:3100 npm run build`
and `npx next start -p 3100`: 24 static pages generated in 8 s, every page tested returned 200
(`/`, `/lines/Red`, `/pipeline`, `/forecast`, `/slow-zones`, `/lines/Red/budget`, `/sitemap.xml`,
`/robots.txt`, `/opengraph-image`). Lighthouse 12 (desktop defaults of the CLI): `/` performance 96, accessibility 100,
best practices 100, SEO 100; `/lines/Red` 95/100/100/100; `/pipeline` 96/100/100/100. axe (wcag2a, 2aa,
21aa, best-practice) on 11 pages: 0 violations on 9; `/lines/Red` has `page-has-heading-one` (1 node)
and `/about` has `link-in-text-block` (1 node). Neither is fixed here; fix before launch if you want a
clean axe run. The home page's `og:title` is "Lines", not the site name.

**What the site needs.**

| Item | Value |
| --- | --- |
| Root directory | `web` |
| Framework | Next.js (auto-detected); install `npm ci`, build `npm run build`, no output directory override |
| Node | `engines.node` is `>=20`; set the project's Node version to **22.x** (Settings > General > Node.js Version) so it does not drift. Local tests ran on Node 26 |
| `HEADWAY_API_URL` | `terraform output -raw api_url`, no trailing slash. Server side only; Production **and** Preview |
| `NEXT_PUBLIC_SITE_URL` | the final public origin, e.g. `https://<project>.vercel.app` or the custom domain. Drives canonical URLs, Open Graph/Twitter cards, `sitemap.xml`, `robots.txt`. Set it before the first build: it is inlined at build time |
| `NEXT_PUBLIC_REPO_URL` | optional, GitHub repo link |
| `vercel.json` | not needed (no rewrites, headers or cron) |
| CORS | pages render on the server, so `api_cors_origins` is not needed for the site to work; add the Vercel origin anyway for browser calls and `/docs`, then `make deploy ENABLE_R4=true` |
| OG image, sitemap, robots | `opengraph-image`, `twitter-image`, `icon.svg`, `sitemap.xml` (7 pages plus lines) and `robots.txt` are generated by the app; no files to add |

**Flag: the API must be reachable when Vercel builds.** The build prerenders 24 pages from the API
(`/` and the lines pages are static with a 5-minute revalidate). A build with `HEADWAY_API_URL`
unset or down fails or bakes in an error state. Deploy R4 first, check `/v1/meta`, then create the project.

**Exact steps on vercel.com (you do these):**
1. Add New > Project > Import the GitHub repository `headway`.
2. Root Directory: Edit > `web`. Framework Preset: Next.js. Leave the build and install commands on their defaults.
3. Environment Variables: add `HEADWAY_API_URL` (Production, Preview) and `NEXT_PUBLIC_SITE_URL`
   (Production; use the `*.vercel.app` URL first, then change it when the domain is attached).
4. Deploy. Settings > General > Node.js Version: 22.x (redeploy if you changed it).
5. Open the deployment URL and check `/`, `/lines/Red`, `/pipeline`, `/sitemap.xml`, `/robots.txt`.
6. Put the production origin in `api_cors_origins` (terraform.tfvars), `make deploy ENABLE_R4=true`,
   then `curl -si -H 'Origin: https://<domain>' "$API/v1/slo" | grep -i access-control`.
7. If you attach a domain: Settings > Domains > Add; update `NEXT_PUBLIC_SITE_URL`, redeploy, and update `api_cors_origins`.
CLI alternative: `cd web && npx vercel link && npx vercel env add HEADWAY_API_URL production && npx vercel --prod`.

**Risks, in one place.** Corrected-vs-uncorrected examples frozen by v1 (see D); first transform
duration unknown on production; v3 history objects cannot be deleted by the role; train job
untested on real data and no alarm; `make deploy` verifies only curate and transform tags; the
site build needs the API live; axe findings above; reserved concurrency 3 and throttle 10/20 will
cap a traffic spike (5xx alarm fires at 5 in 5 minutes).
