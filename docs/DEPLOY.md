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
