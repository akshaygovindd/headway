# Curate, static GTFS and transform Lambdas (container images, arm64).

locals {
  curate_name    = "headway-curate"
  static_name    = "headway-static"
  transform_name = "headway-transform"
  gold_prefix    = "public/${var.city_id}/gold"
  history_prefix = "public/${var.city_id}/history"
  motherduck_ssm = "/headway/motherduck_token"
  motherduck_arn = "arn:aws:ssm:${data.aws_region.current.name}:${local.account_id}:parameter${local.motherduck_ssm}"
  # The incident-summary LLM key (Release 3): created by hand, never by Terraform.
  openai_ssm = "/headway/openai_api_key"
  openai_arn = "arn:aws:ssm:${data.aws_region.current.name}:${local.account_id}:parameter${local.openai_ssm}"
  # The chaos switch (ADR-017) is created by hand when a drill needs it, never by Terraform; the
  # ingest and transform roles may read it, nothing may write it.
  chaos_ssm    = "/headway/chaos"
  chaos_arn    = "arn:aws:ssm:${data.aws_region.current.name}:${local.account_id}:parameter${local.chaos_ssm}"
  lock_key     = "state/${var.city_id}/transform.lock"
  state_prefix = "state/transform_runs"
  # The Elementary history (dbt/headway_transform/observability.py) is kept in state/elementary_*/
  # folders (elementary_test_results, elementary_data_monitoring_metrics), listed, read and appended
  # by the same state module as transform_runs.
  elementary_state_prefix = "state/elementary_*"
  # Release 3: the closed-day forecast tables (forecast_examples, forecast_gate_days) are written
  # once each by the transform and read by the training Lambda (dbt/headway_transform/forecast_state.py).
  forecast_state_prefix = "state/forecast_*"
  # Release 3: the incident summaries (state/incident_summaries), so a version is summarised once and
  # the daily LLM call cap holds across runs (dbt/headway_transform/incident_summaries.py).
  incident_state_prefix = "state/incident_summaries"
  state_prefixes = concat(
    [local.state_prefix, local.elementary_state_prefix],
    var.enable_r3 ? [local.forecast_state_prefix, local.incident_state_prefix] : [],
  )
  # Release 3 serving: the transform reads the model registry (latest.json, the model files) and
  # the training job's reports. Read and list only; the training role writes them (train.tf).
  forecast_model_prefix = "models/forecast/*"
  raw_data_prefix = concat(
    ["curated/*", "manifests/*", "quarantine/*", local.lock_key],
    [for p in local.state_prefixes : "${p}/*"],
    var.enable_r3 ? [local.forecast_model_prefix] : [],
  )
}

data "aws_region" "current" {}

# ---------------------------------------------------------------- log groups

resource "aws_cloudwatch_log_group" "curate" {
  name              = "/aws/lambda/${local.curate_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "static" {
  name              = "/aws/lambda/${local.static_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "transform" {
  name              = "/aws/lambda/${local.transform_name}"
  retention_in_days = 14
}

# ---------------------------------------------------------------- roles

resource "aws_iam_role" "curate" {
  name               = "${local.curate_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role" "static" {
  name               = "${local.static_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role" "transform" {
  name               = "${local.transform_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# ---------------------------------------------------------------- policies

data "aws_iam_policy_document" "curate" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.curate.arn}:*"]
  }

  statement {
    sid       = "ListRaw"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.raw.arn]
  }

  statement {
    sid     = "ReadRawManifestsAndCurated"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.raw.arn}/raw/*",
      "${aws_s3_bucket.raw.arn}/manifests/*",
      "${aws_s3_bucket.raw.arn}/curated/*",
      "${aws_s3_bucket.raw.arn}/quarantine/*",
      "${aws_s3_bucket.raw.arn}/state/*",
    ]
  }

  statement {
    sid     = "WriteCuratedQuarantineAndState"
    actions = ["s3:PutObject", "s3:DeleteObject"]
    resources = [
      "${aws_s3_bucket.raw.arn}/curated/*",
      "${aws_s3_bucket.raw.arn}/quarantine/*",
      "${aws_s3_bucket.raw.arn}/state/*",
    ]
  }
}

resource "aws_iam_role_policy" "curate" {
  name   = "curate"
  role   = aws_iam_role.curate.id
  policy = data.aws_iam_policy_document.curate.json
}

data "aws_iam_policy_document" "static" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.static.arn}:*"]
  }

  # List lets the existence check tell a missing object from a denied one.
  statement {
    sid       = "ListRaw"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.raw.arn]
  }

  statement {
    sid       = "CheckLoadedStaticVersions"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.raw.arn}/curated/${var.city_id}/static/*"]
  }

  statement {
    sid     = "WriteRawAndCuratedStatic"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.raw.arn}/raw/${var.city_id}/feed=gtfs_static/*",
      "${aws_s3_bucket.raw.arn}/curated/${var.city_id}/static/*",
    ]
  }

  # Daily weather for the Release 3 weather comparison (ADR-022): one part per date.
  dynamic "statement" {
    for_each = var.enable_r3 ? [1] : []
    content {
      sid       = "WriteCuratedWeather"
      actions   = ["s3:PutObject"]
      resources = ["${aws_s3_bucket.raw.arn}/curated/${var.city_id}/table=weather_daily/*"]
    }
  }
}

resource "aws_iam_role_policy" "static" {
  name   = "static"
  role   = aws_iam_role.static.id
  policy = data.aws_iam_policy_document.static.json
}

data "aws_iam_policy_document" "transform" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.transform.arn}:*"]
  }

  statement {
    sid       = "ListRawInputs"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.raw.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = local.raw_data_prefix
    }
  }

  statement {
    sid       = "ReadRawInputs"
    actions   = ["s3:GetObject"]
    resources = [for p in local.raw_data_prefix : "${aws_s3_bucket.raw.arn}/${p}"]
  }

  # Overlapping runs are prevented with a conditional-write lock object (see the transform handler).
  statement {
    sid       = "WriteRunLock"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.raw.arn}/${local.lock_key}"]
  }

  # Run state persisted between runs (agg_transform_runs and other state tables): read at startup,
  # appended at the end of a run. Listing and reading are in the raw-input statements above.
  statement {
    sid       = "WriteRunState"
    actions   = ["s3:PutObject"]
    resources = [for p in local.state_prefixes : "${aws_s3_bucket.raw.arn}/${p}/*"]
  }

  statement {
    sid       = "ListGold"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.public.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.gold_prefix}/*"]
    }
  }

  statement {
    sid = "ReadAndWriteGold"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.public.arn}/${local.gold_prefix}/*"]
  }

  # Release 2 history (ADR-015): immutable daily aggregates, created once each with a conditional
  # put. No delete and no overwrite permission beyond what a conditional create needs.
  statement {
    sid       = "ListHistory"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.public.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.history_prefix}/*"]
    }
  }

  statement {
    sid       = "ReadAndWriteHistory"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.public.arn}/${local.history_prefix}/*"]
  }

  statement {
    sid       = "ReadMotherDuckToken"
    actions   = ["ssm:GetParameter"]
    resources = [local.motherduck_arn]
  }

  statement {
    sid       = "ReadChaosSwitch"
    actions   = ["ssm:GetParameter"]
    resources = [local.chaos_arn]
  }

  # The incident-summary LLM key (Release 3). The parameter is created by hand, never by Terraform;
  # without it the summaries use the deterministic template. SecureString under the default key.
  statement {
    sid       = "ReadOpenAiKey"
    actions   = ["ssm:GetParameter"]
    resources = [local.openai_arn]
  }

  # Cost Explorer has no resource-level permissions.
  statement {
    sid       = "ReadCosts"
    actions   = ["ce:GetCostAndUsage"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "transform" {
  name   = "transform"
  role   = aws_iam_role.transform.id
  policy = data.aws_iam_policy_document.transform.json
}

# ---------------------------------------------------------------- functions

resource "aws_lambda_function" "curate" {
  function_name = local.curate_name
  role          = aws_iam_role.curate.arn
  package_type  = "Image"
  image_uri     = var.curate_image_uri
  architectures = ["arm64"]
  memory_size   = 1024
  timeout       = 300

  environment {
    variables = {
      RAW_BUCKET = aws_s3_bucket.raw.bucket
      CITY_ID    = var.city_id
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.curate.name
  }

  depends_on = [aws_iam_role_policy.curate]
}

resource "aws_lambda_function" "static" {
  function_name = local.static_name
  role          = aws_iam_role.static.arn
  package_type  = "Image"
  image_uri     = var.curate_image_uri
  architectures = ["arm64"]
  memory_size   = 3008
  timeout       = 900

  image_config {
    command = ["headway.curate.handler.static_handler"]
  }

  environment {
    variables = {
      RAW_BUCKET = aws_s3_bucket.raw.bucket
      CITY_ID    = var.city_id
      # Release 3: also fetch the daily weather (ADR-022).
      HEADWAY_ENABLE_R3 = tostring(var.enable_r3)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.static.name
  }

  depends_on = [aws_iam_role_policy.static]
}

resource "aws_lambda_function" "transform" {
  function_name = local.transform_name
  role          = aws_iam_role.transform.arn
  package_type  = "Image"
  image_uri     = var.transform_image_uri
  architectures = ["arm64"]
  memory_size   = 3008
  timeout       = 900

  ephemeral_storage {
    size = 10240
  }

  environment {
    variables = {
      RAW_BUCKET    = aws_s3_bucket.raw.bucket
      PUBLIC_BUCKET = aws_s3_bucket.public.bucket
      CITY_ID       = var.city_id
      LOOKBACK_DAYS = tostring(var.lookback_days)
      # DuckDB limits (ADR-024): the account caps Lambda memory at 3008 MB, so bound DuckDB
      # well under it and let it spill to /tmp (ephemeral storage above).
      HEADWAY_DUCKDB_MEMORY_LIMIT = var.transform_duckdb_memory_limit
      HEADWAY_DUCKDB_THREADS      = tostring(var.transform_duckdb_threads)
      HEADWAY_DUCKDB_MAX_TEMP     = var.transform_duckdb_max_temp
      # Release 2 dbt models (tag r2) are skipped unless this is "true"; see docs/DEPLOY.md
      HEADWAY_ENABLE_R2 = tostring(var.enable_r2)
      # Release 3 dbt models (tag r3) are skipped unless this is "true"; they need enable_r2
      HEADWAY_ENABLE_R3 = tostring(var.enable_r3)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.transform.name
  }

  depends_on = [aws_iam_role_policy.transform]
}

# Scheduled runs are never retried: the next schedule picks up the work.
resource "aws_lambda_function_event_invoke_config" "pipeline" {
  for_each = {
    curate    = aws_lambda_function.curate.function_name
    static    = aws_lambda_function.static.function_name
    transform = aws_lambda_function.transform.function_name
  }

  function_name          = each.value
  maximum_retry_attempts = 0
}
