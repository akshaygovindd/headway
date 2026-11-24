# Release 3: weekly forecast training (ADR-019). Everything here exists only with enable_r3.
#
# The function reads the closed-day examples and gate days the transform keeps in the raw bucket
# (state/forecast_*), logs to an MLflow SQLite database that lives in the raw bucket (mlflow/) and
# registers models under models/forecast/. It runs without dbt, DuckDB extensions or network access
# beyond S3.

locals {
  train_name = "headway-train"
  # Sized from a local run on the container image: 56 days at the 8000 rows per day cap (448k
  # examples) took 43 s and peaked near 2.3 GiB (docs/RUNBOOK.md, Training job).
  train_memory_mb = 4096
  train_timeout_s = 300
}

resource "aws_cloudwatch_log_group" "train" {
  count = var.enable_r3 ? 1 : 0

  name              = "/aws/lambda/${local.train_name}"
  retention_in_days = 14
}

resource "aws_iam_role" "train" {
  count = var.enable_r3 ? 1 : 0

  name               = "${local.train_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "train" {
  count = var.enable_r3 ? 1 : 0

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.train[0].arn}:*"]
  }

  # Listing is scoped to the three prefixes the job touches.
  statement {
    sid       = "ListTrainingPrefixes"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.raw.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["state/forecast_*", "mlflow/*", "models/forecast/*"]
    }
  }

  # The closed-day examples and gate days: read only.
  statement {
    sid       = "ReadForecastState"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.raw.arn}/state/forecast_*/*"]
  }

  # MLflow database, its artifacts and the single-writer lock; the model registry.
  statement {
    sid     = "ReadWriteMlflowAndModels"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "${aws_s3_bucket.raw.arn}/mlflow/*",
      "${aws_s3_bucket.raw.arn}/models/forecast/*",
    ]
  }
}

resource "aws_iam_role_policy" "train" {
  count = var.enable_r3 ? 1 : 0

  name   = "train"
  role   = aws_iam_role.train[0].id
  policy = data.aws_iam_policy_document.train[0].json
}

resource "aws_lambda_function" "train" {
  count = var.enable_r3 ? 1 : 0

  function_name = local.train_name
  role          = aws_iam_role.train[0].arn
  package_type  = "Image"
  image_uri     = var.train_image_uri
  architectures = ["arm64"]
  memory_size   = local.train_memory_mb
  timeout       = local.train_timeout_s

  ephemeral_storage {
    size = 1024
  }

  environment {
    variables = {
      RAW_BUCKET = aws_s3_bucket.raw.bucket
      CITY_ID    = var.city_id
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.train[0].name
  }

  depends_on = [aws_iam_role_policy.train]
}

# A weekly run is never retried: the next week's run takes the same data plus one more week.
resource "aws_lambda_function_event_invoke_config" "train" {
  count = var.enable_r3 ? 1 : 0

  function_name          = aws_lambda_function.train[0].function_name
  maximum_retry_attempts = 0
}

resource "aws_scheduler_schedule" "train" {
  count = var.enable_r3 ? 1 : 0

  name = "${local.train_name}-weekly"
  # Monday 06:00 UTC: after the Sunday service day has closed (06:00 local on Monday) and the
  # hourly transform has written it. UTC schedules do not follow daylight saving time.
  schedule_expression = "cron(0 6 ? * MON *)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.train[0].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{}"

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}
