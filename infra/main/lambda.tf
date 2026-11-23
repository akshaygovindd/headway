locals {
  function_name = "headway-ingest-rt"
  live_prefix   = "public/${var.city_id}/live"
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = 14
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest" {
  name               = "${local.function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "ingest" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }

  statement {
    sid     = "WriteRawAndManifests"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.raw.arn}/raw/*",
      "${aws_s3_bucket.raw.arn}/manifests/*",
    ]
  }

  statement {
    sid       = "WriteLiveSnapshot"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.public.arn}/${local.live_prefix}/*"]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "ingest"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

resource "aws_lambda_function" "ingest" {
  function_name = local.function_name
  role          = aws_iam_role.ingest.arn
  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = "headway.ingest.handler.lambda_handler"
  # 128 MB is the target (the vehicle poll loop holds one feed at a time; `make images-smoke`
  # checks peak memory under a 128 MB limit). Fall back to 256 if real runs get close. The
  # timeout covers a run that waits for the 15 s polls: ~48 s typical, ended by 55 s at the latest.
  memory_size = 128
  timeout     = 58

  filename         = "${path.module}/../../build/ingest.zip"
  source_code_hash = filebase64sha256("${path.module}/../../build/ingest.zip")

  # One run at a time: overlapping runs would double-capture the same minute.

  environment {
    variables = {
      RAW_BUCKET         = aws_s3_bucket.raw.bucket
      PUBLIC_BUCKET      = aws_s3_bucket.public.bucket
      CITY_ID            = var.city_id
      HEADWAY_CONFIG_DIR = "/var/task/config/cities"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ingest.name
  }

  depends_on = [aws_iam_role_policy.ingest]
}

resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name                = aws_lambda_function.ingest.function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 60
}
