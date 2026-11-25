# Release 4: the public read-only metric API (ADR-023). Everything here exists only with enable_r4.
#
# A container Lambda (FastAPI + Mangum) behind an HTTP API. It reads only the public gold Parquet
# over HTTPS: its role has a log policy and nothing else (no S3, no SSM, no raw or curated access).
# Its reserved concurrency is its own ceiling and is taken from the account pool, which is why
# enable_r4 refuses to plan until the account limit is confirmed to be at least 100 (variables.tf).

locals {
  api_name = "headway-api"
  # The data root the API reads: <public bucket>/public/<city> (gold/ and history/ under it).
  api_data_url = "https://${aws_s3_bucket.public.bucket_regional_domain_name}/public/${var.city_id}"
}

resource "aws_cloudwatch_log_group" "api" {
  count = var.enable_r4 ? 1 : 0

  name              = "/aws/lambda/${local.api_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "api_access" {
  count = var.enable_r4 ? 1 : 0

  name              = "/aws/apigateway/${local.api_name}-access"
  retention_in_days = 14
}

resource "aws_iam_role" "api" {
  count = var.enable_r4 ? 1 : 0

  name               = "${local.api_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "api" {
  count = var.enable_r4 ? 1 : 0

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "api" {
  count = var.enable_r4 ? 1 : 0

  name   = "api"
  role   = aws_iam_role.api[0].id
  policy = data.aws_iam_policy_document.api[0].json
}

resource "aws_lambda_function" "api" {
  count = var.enable_r4 ? 1 : 0

  function_name                  = local.api_name
  role                           = aws_iam_role.api[0].arn
  package_type                   = "Image"
  image_uri                      = var.api_image_uri
  architectures                  = ["arm64"]
  memory_size                    = 1024
  timeout                        = 15
  reserved_concurrent_executions = var.api_reserved_concurrency

  ephemeral_storage {
    size = 512 # the warm copy of the small gold tables (/tmp)
  }

  environment {
    variables = {
      HEADWAY_DATA_URL = local.api_data_url
      API_CORS_ORIGINS = var.api_cors_origins
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.api[0].name
  }

  lifecycle {
    precondition {
      condition     = var.api_confirmed_concurrency_limit >= 100 + var.api_reserved_concurrency
      error_message = "The account's Lambda concurrency limit must be at least 100 plus the reservation before the API is deployed (aws lambda get-account-settings; docs/DEPLOY.md, Release 4)."
    }
  }

  depends_on = [aws_iam_role_policy.api]
}

resource "aws_apigatewayv2_api" "api" {
  count = var.enable_r4 ? 1 : 0

  name          = local.api_name
  protocol_type = "HTTP"
  # CORS is answered by the application (an allowlist from API_CORS_ORIGINS), not here.
}

resource "aws_apigatewayv2_integration" "api" {
  count = var.enable_r4 ? 1 : 0

  api_id                 = aws_apigatewayv2_api.api[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api[0].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 20000
}

resource "aws_apigatewayv2_route" "api" {
  count = var.enable_r4 ? 1 : 0

  api_id    = aws_apigatewayv2_api.api[0].id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.api[0].id}"
}

resource "aws_apigatewayv2_stage" "api" {
  count = var.enable_r4 ? 1 : 0

  api_id      = aws_apigatewayv2_api.api[0].id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.api_throttle_rate
    throttling_burst_limit = var.api_throttle_burst
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access[0].arn
    format = jsonencode({
      requestId        = "$context.requestId"
      requestTime      = "$context.requestTime"
      httpMethod       = "$context.httpMethod"
      path             = "$context.path"
      status           = "$context.status"
      responseLength   = "$context.responseLength"
      latencyMs        = "$context.responseLatency"
      integrationError = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "api" {
  count = var.enable_r4 ? 1 : 0

  statement_id  = "AllowHttpApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api[0].execution_arn}/*/*"
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  count = var.enable_r4 ? 1 : 0

  alarm_name          = "${local.api_name}-5xx"
  alarm_description   = "The public API returned 5 or more 5xx responses in 5 minutes. Runbook: https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#alarm-headway-api-5xx"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = aws_apigatewayv2_api.api[0].id }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
