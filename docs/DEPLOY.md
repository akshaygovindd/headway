# Deploying the stack

This creates the buckets, the `headway-ingest-rt` Lambda, a one-minute schedule, alarms and a
cost budget (section 8 adds the curate, static and transform Lambdas) in AWS account region `us-east-1`. Run every command from the repository root unless
a `cd` is shown.

## Prerequisites

- AWS credentials for the target account in your shell (`aws sts get-caller-identity` works).
- `terraform` >= 1.10, `uv`, `make`.
- Account note: the ingest Lambda sets no reserved concurrency, because an account with the
  default limit of 10 cannot reserve any. Runs take seconds once a minute and the scheduler
  never retries, so overlapping runs are unlikely.

## 1. Bootstrap the state bucket (once, local state)

```sh
cd infra/bootstrap
terraform init
terraform apply
terraform output -raw tfstate_bucket     # note this name
cd ../..
```

The bootstrap state file stays local in `infra/bootstrap/` (gitignored). Keep it.

## 2. Configure the main stack backend

```sh
cp infra/main/backend.hcl.example infra/main/backend.hcl
```

Edit `infra/main/backend.hcl` and replace `<account_id>` so `bucket` equals the bootstrap output.
`backend.hcl` is gitignored.

## 3. Set variables

```sh
cp infra/main/terraform.tfvars.example infra/main/terraform.tfvars
```

Edit `infra/main/terraform.tfvars`: set `alert_email` and `budget_email`. `terraform.tfvars` is
gitignored.

## 4. Build the Lambda zip

```sh
make lambda-zip          # writes build/ingest.zip (arm64, Python 3.12)
```

## 5. Init, plan, apply

```sh
cd infra/main
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan
terraform apply tfplan
cd ../..
```

After applying, rebuild and re-apply whenever the code changes (`make lambda-zip`, then
`terraform plan` and `apply`); the function updates when the zip hash changes.

## 6. One-time manual steps

- Confirm the SNS email subscription: click the link in the "AWS Notification - Subscription
  Confirmation" email sent to `alert_email`. Alarm mails do not arrive until you do.
- Activate the `project` cost allocation tag (Billing and Cost Management, Cost allocation tags,
  user-defined tags). The budget filters on `user:project$headway` and sees no spend until the
  tag is active (activation can take up to 24 hours).

## 7. Verify

Wait two or three minutes after `apply`, then:

```sh
RAW=$(terraform -chdir=infra/main output -raw raw_bucket)
aws s3 ls "s3://$RAW/manifests/boston/" --recursive | tail -5
aws s3 ls "s3://$RAW/raw/boston/" --recursive | tail -6

URL=$(terraform -chdir=infra/main output -raw public_snapshot_url)
curl -s -D - "$URL" -o /dev/null | grep -i -E "^(HTTP|content-type|cache-control)"
curl -s "$URL" | head -c 600
```

Expected: a new manifest roughly every minute, three raw objects per run, and a JSON snapshot with
`vehicle_count` greater than zero, `Content-Type: application/json` and `Cache-Control: max-age=30`.

Lambda logs, one JSON summary line per run:

```sh
aws logs tail /aws/lambda/headway-ingest-rt --since 10m
```

## 8. Curate, static and transform Lambdas (Phase 5b)

These three Lambdas run from container images (linux/arm64). `terraform.tfvars` must hold the two
image URIs, but the ECR repositories that receive the images are created by the same stack, so the
first apply is split. Docker must be running. Run in this order:

```sh
# 1. MotherDuck token (SecureString; Terraform only grants read access to it).
aws ssm put-parameter --name /headway/motherduck_token --type SecureString \
  --value '<YOUR_MOTHERDUCK_TOKEN>'

# 2. Create only the ECR repositories (placeholder URIs satisfy the required variables).
cd infra/main
terraform apply -target=aws_ecr_repository.pipeline -target=aws_ecr_lifecycle_policy.pipeline \
  -var curate_image_uri=placeholder -var transform_image_uri=placeholder
cd ../..

# 3. Build both images, log in to ECR, tag with the git short sha and push. Prints the URIs.
make images-build
make images-push
```

4. Copy the two printed lines into `infra/main/terraform.tfvars`:

```hcl
curate_image_uri    = "<account>.dkr.ecr.us-east-1.amazonaws.com/headway-curate:<sha>"
transform_image_uri = "<account>.dkr.ecr.us-east-1.amazonaws.com/headway-transform:<sha>"
```

5. Plan and apply:

```sh
cd infra/main
terraform plan -out=tfplan
terraform apply tfplan
cd ../..
```

This also moves the ingest schedule to `cron(* * * * ? *)`, so runs align to the minute.

6. Seed the pipeline once, in this order. Curate fails until a static GTFS version is loaded, and
transform needs curated data:

```sh
aws lambda invoke --function-name headway-static --cli-read-timeout 330 /dev/stdout
aws lambda invoke --function-name headway-curate --cli-read-timeout 330 /dev/stdout
aws lambda invoke --function-name headway-transform --cli-read-timeout 630 /dev/stdout
```

After that the schedules take over: curate every 15 minutes, compaction at :05, transform at :20,
static daily at 09:00 UTC (04:00 EST, 05:00 EDT). To redeploy new code, run `make images-push`,
update the URIs in `terraform.tfvars`, then plan and apply.

### Verify Phase 5b

```sh
aws logs tail /aws/lambda/headway-curate --since 30m
aws logs tail /aws/lambda/headway-transform --since 2h

RAW=$(terraform -chdir=infra/main output -raw raw_bucket)
PUBLIC=$(terraform -chdir=infra/main output -raw public_bucket)
aws s3 ls "s3://$RAW/curated/boston/" --recursive | tail -5
aws s3 ls "s3://$PUBLIC/public/boston/gold/"
aws s3 cp "s3://$PUBLIC/public/boston/gold/manifest.json" - | head -c 600
```

Expected: curate logs one JSON line per run; the gold folder holds eight parquet files plus
`manifest.json`, and `cost_daily.parquet` after the first transform run at or after 06:00 UTC
(Cost Explorer must be enabled for the account; the `project` tag must be active, see section 6).
The transform summary line reports `dbt_status`, `tests_failed`, `rows_exported`, step durations
and `cost_rows`. dbt test failures do not stop the export; they are counted in that line.

MotherDuck: in the MotherDuck UI or CLI, `SHOW TABLES FROM headway;` lists the gold tables (the
`headway` database is created by the first publish).

Point the app at the published data:

```sh
echo "HEADWAY_DATA_URL=$(terraform -chdir=infra/main output -raw gold_url | sed 's#/gold$##')"
```

## Tear down

```sh
cd infra/main && terraform destroy
```

Both data buckets must be emptied first (`aws s3 rm s3://<bucket> --recursive`). The bootstrap
bucket is versioned; remove it manually if you really want to delete state history.
