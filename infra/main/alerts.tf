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

# Ingest writes FeedErrorCount as an embedded-metric-format log line (namespace Headway, dimension
# Feed): a failed fetch is not a Lambda error, so the Errors alarm never sees a feed outage.
resource "aws_cloudwatch_metric_alarm" "feed_errors" {
  alarm_name          = "headway-feed-errors"
  alarm_description   = "A realtime feed failed in at least 5 of the last 10 minutes. Runbook: https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#alarm-headway-feed-errors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 10
  datapoints_to_alarm = 5
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "worst"
    expression  = "MAX([FILL(trip_updates, 0), FILL(vehicle_positions, 0), FILL(alerts, 0)])"
    label       = "Worst feed errors per minute"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = ["trip_updates", "vehicle_positions", "alerts"]

    content {
      id = metric_query.value

      metric {
        namespace   = "Headway"
        metric_name = "FeedErrorCount"
        dimensions  = { Feed = metric_query.value }
        period      = 60
        stat        = "Sum"
      }
    }
  }
}

# The transform writes these as embedded-metric-format log lines at the end of each hourly run
# (namespace Headway, no dimensions), so they need no PutMetricData permission.
resource "aws_cloudwatch_metric_alarm" "gold_stale" {
  alarm_name          = "headway-gold-stale"
  alarm_description   = "Gold was replaced more than 90 minutes after the previous export, two hours running, or the transform stopped reporting. Runbook: https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#alarm-headway-gold-stale"
  namespace           = "Headway"
  metric_name         = "GoldAgeMinutes"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 90
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "missed_minutes" {
  alarm_name          = "headway-missed-minutes"
  alarm_description   = "Ingest missed 5 or more of the last 60 minutes. Runbook: https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#alarm-headway-missed-minutes"
  namespace           = "Headway"
  metric_name         = "MissedRunsLastHour"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 5
  # A transform that stops reporting is the job of headway-transform-not-succeeded and
  # headway-gold-stale; this alarm only judges hours the transform did measure.
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
}

# Release 2 only: the metric exists only with enable_r2, and only in service hours (05:00 to 01:00
# local), so the quiet night hours keep the alarm in its current state instead of breaching.
resource "aws_cloudwatch_metric_alarm" "arrival_coverage_low" {
  count = var.enable_r2 ? 1 : 0

  alarm_name          = "headway-arrival-coverage-low"
  alarm_description   = "Fewer than 70% of scheduled stops had a usable reconstructed arrival for 3 hours in a row. Runbook: https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#alarm-headway-arrival-coverage-low"
  namespace           = "Headway"
  metric_name         = "ArrivalCoveragePct"
  statistic           = "Minimum"
  period              = 3600
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  comparison_operator = "LessThanThreshold"
  threshold           = 70
  treat_missing_data  = var.enable_r2 ? "missing" : "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
