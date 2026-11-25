# Deploying the stack

A walkthrough from a fresh clone to a running pipeline in AWS account region `us-east-1`. It
creates the buckets, the ingest, curate, static and transform Lambdas (the last three as container
images), their schedules, alarms and a cost budget. Run every command from the repository root
unless a `cd` is shown. Fill in the [walkthrough record](#walkthrough-record) as you go.

## Prerequisites

- AWS credentials for the target account in your shell (`aws sts get-caller-identity` works).
- `terraform` >= 1.10, `uv`, `make`, `docker` (running) with buildx.
- A MotherDuck account and token.
- Account note: the ingest Lambda sets no reserved concurrency, because an account with the
  default limit of 10 cannot reserve any. Runs take seconds once a minute and the scheduler
  never retries, so overlapping runs are unlikely.

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

Both files are gitignored.

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
stops before the apply). To redeploy new code later, run `make lambda-zip` (if ingest changed)
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
- a gold folder with eight parquet files plus `manifest.json`, and `cost_daily.parquet` after the
  first transform run at or after 06:00 UTC;
- one JSON summary line per run in the logs. The transform line reports `dbt_status`,
  `tests_failed`, `rows_exported`, `cost_rows` and `step_ms` (`s3_load`, `dbt_build`, `dbt_test`,
  `export_gold`, `cost`, `publish`). dbt test failures do not stop the export; they are counted.

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
| Prerequisites and clone | | |
| 1. Bootstrap state bucket | | |
| 2. Backend and tfvars | | |
| 3. MotherDuck token (SSM) | | |
| 4. Ingest zip and ECR repositories | | |
| 5. `make deploy` | | |
| 6. `make seed` | | |
| 7. Manual steps (SNS, cost tag) | | |
| 8. Verify | | |
| **Total** | | |

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
a minute before fetching (a second delivery fetches nothing). The ingest function is 128 MB with
a 58 s timeout, which fits both settings.

Switch-on, Phase 2.6, in this order:

1. Static geometry: invoke `headway-static` once. The latest feed version is already loaded, so it
   only writes `shape_points_dist` and `stop_shape_positions` for it (check both exist under
   `curated/boston/static/`).
2. Set `enable_r2 = true` in `terraform.tfvars`, `make deploy`, and wait for one transform run:
   `fct_reconstructed_arrivals`, `agg_arrival_reconstruction_daily` and the Release 2 metric models
   (accuracy, actual deviation, headway adherence, ETT, active alerts) exist, the gold export gains
   those six aggregates (and only then; the Accuracy page and the Line explorer headway view appear
   when the files do), and its logged step times are in line with the earlier runs. Run this on
   1-minute data first.
3. Set `vehicle_polls = 4`, `make deploy`. Check the next manifests: six entries, `poll_index`
   0-3, a run of 45-58 s, memory under about 100 MB in the Lambda report line, the live snapshot
   updating every 15 s. If memory is close to the limit, set `memory_size` to 256 and deploy again.
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
   `{"task":"rebuild_history","from":...,"to":...,"metric_version":2}` (at most 14 days per call),
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
  --payload '{"task":"rebuild_history","from":"2026-11-16","to":"2026-11-22","metric_version":2}' out.json
```

A backfill reads as many days of curated data as separate it from today, inside the 900 s limit of
the function; split a long range into calls of a few days if one times out.

## Release 4 switch: public metric API (off by default)

Phase 4.1 is built and tested but not deployed. `enable_r4 = true` adds the API Lambda (container,
arm64), an HTTP API with throttling and an access log, an ECR repository `headway-api` and a 5xx
alarm (ADR-023, `docs/API.md`). It needs no new secrets and no access to any bucket.

**Hard prerequisite: do not enable R4 until the account's Lambda concurrency limit is at least 100.**
The API function reserves concurrency (`api_reserved_concurrency`, default 3) out of the account
pool, and Lambda keeps 100 unreserved. On an account still at the default limit of 10 the reservation
cannot be made, and one that could would starve ingest, curate and transform. Terraform therefore
requires the confirmed limit to be at least 100 plus `api_reserved_concurrency` (103 by default;
request 1000).

```sh
aws lambda get-account-settings --query 'AccountLimit.[ConcurrentExecutions,UnreservedConcurrentExecutions]'
```

If `ConcurrentExecutions` prints less than 100: Service Quotas > AWS Lambda > Concurrent
executions > request an increase to 1000, and wait for approval. Then set the value the command
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
2. Set `HEADWAY_API_URL` to `terraform output -raw api_url` (no trailing slash). Pages are rendered on the
   server, so the API's `API_CORS_ORIGINS` does not need the site's origin unless a browser calls the API directly.
3. Pages revalidate every 300 s, matching the API's `Cache-Control: public, max-age=300`.
4. Check `/`, `/lines/Red` and `/pipeline` against the live API, then attach the domain.

See [web/README.md](../web/README.md).

