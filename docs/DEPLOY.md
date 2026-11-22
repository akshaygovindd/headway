# Deploying the ingest stack

This creates the buckets, the `headway-ingest-rt` Lambda, a one-minute schedule, alarms and a
cost budget in AWS account region `us-east-1`. Run every command from the repository root unless
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

## Tear down

```sh
cd infra/main && terraform destroy
```

Both data buckets must be emptied first (`aws s3 rm s3://<bucket> --recursive`). The bootstrap
bucket is versioned; remove it manually if you really want to delete state history.
