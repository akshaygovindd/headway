resource "aws_sns_topic" "alerts" {
  name = "headway-alerts"
}

resource "aws_sns_topic_subscription" "alert_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "ingest_errors" {
  alarm_name          = "${local.function_name}-errors"
  alarm_description   = "Ingest Lambda failed 3 or more times in 15 minutes. Runbook: https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#alarm-headway-ingest-rt-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.ingest.function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 3
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "ingest_invocations_low" {
  alarm_name          = "${local.function_name}-invocations-low"
  alarm_description   = "Ingest Lambda ran fewer than 10 times in 15 minutes (expected ~15). Runbook: https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#alarm-headway-ingest-rt-invocations-low"
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = aws_lambda_function.ingest.function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 10
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "curate_errors" {
  alarm_name          = "${local.curate_name}-errors"
  alarm_description   = "Curate Lambda failed at least once in 15 minutes. Runbook: https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#alarm-headway-curate-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.curate.function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "static_errors" {
  alarm_name          = "${local.static_name}-errors"
  alarm_description   = "Static GTFS Lambda failed in the last day. Runbook: https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#alarm-headway-static-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.static.function_name }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "transform_errors" {
  alarm_name          = "${local.transform_name}-errors"
  alarm_description   = "Transform Lambda failed at least once in the hourly run window. Runbook: https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#alarm-headway-transform-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.transform.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "transform_stale" {
  alarm_name          = "${local.transform_name}-not-succeeded"
  alarm_description   = "Transform Lambda has not completed successfully in 2 hours. Runbook: https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#alarm-headway-transform-not-succeeded"
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  evaluation_periods  = 1
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "successes"
    expression  = "invocations - errors"
    label       = "Successful runs"
    return_data = true
  }

  metric_query {
    id = "invocations"

    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Invocations"
      dimensions  = { FunctionName = aws_lambda_function.transform.function_name }
      period      = 7200
      stat        = "Sum"
    }
  }

  metric_query {
    id = "errors"

    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      dimensions  = { FunctionName = aws_lambda_function.transform.function_name }
      period      = 7200
      stat        = "Sum"
    }
  }
}
