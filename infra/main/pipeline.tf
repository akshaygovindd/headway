# Curate, static GTFS and transform Lambdas (container images, arm64).

locals {
  curate_name     = "headway-curate"
  static_name     = "headway-static"
  transform_name  = "headway-transform"
  gold_prefix     = "public/${var.city_id}/gold"
  motherduck_ssm  = "/headway/motherduck_token"
  motherduck_arn  = "arn:aws:ssm:${data.aws_region.current.name}:${local.account_id}:parameter${local.motherduck_ssm}"
  raw_data_prefix = ["curated/*", "manifests/*", "quarantine/*"]
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

  statement {
    sid       = "ReadMotherDuckToken"
    actions   = ["ssm:GetParameter"]
    resources = [local.motherduck_arn]
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
  timeout       = 600

  ephemeral_storage {
    size = 4096
  }

  environment {
    variables = {
      RAW_BUCKET    = aws_s3_bucket.raw.bucket
      PUBLIC_BUCKET = aws_s3_bucket.public.bucket
      CITY_ID       = var.city_id
      LOOKBACK_DAYS = tostring(var.lookback_days)
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
