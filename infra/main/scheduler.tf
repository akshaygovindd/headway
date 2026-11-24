data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.function_name}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler_invoke" {
  statement {
    actions = ["lambda:InvokeFunction"]
    resources = concat(
      [
        aws_lambda_function.ingest.arn,
        aws_lambda_function.curate.arn,
        aws_lambda_function.static.arn,
        aws_lambda_function.transform.arn,
      ],
      aws_lambda_function.train[*].arn,
    )
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "invoke-pipeline"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_invoke.json
}

resource "aws_scheduler_schedule" "ingest" {
  name = "${local.function_name}-every-minute"
  # Aligned to the minute so runs land on :00 (a rate() expression drifts from creation time).
  schedule_expression = "cron(* * * * ? *)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ingest.arn
    role_arn = aws_iam_role.scheduler.arn
    # Literal string: jsonencode() would escape < and > and block substitution.
    input = "{\"scheduled_at\": \"<aws.scheduler.scheduled-time>\"}"

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

# ---------------------------------------------------------------- pipeline schedules

locals {
  pipeline_schedules = {
    curate = {
      expression = "rate(15 minutes)"
      function   = aws_lambda_function.curate.arn
      input      = "{}"
    }
    compact = {
      expression = "cron(5 * * * ? *)"
      function   = aws_lambda_function.curate.arn
      input      = "{\"task\":\"compact\"}"
    }
    transform = {
      expression = "cron(20 * * * ? *)"
      function   = aws_lambda_function.transform.arn
      input      = "{}"
    }
    static = {
      # 09:00 UTC is 04:00 America/New_York in winter (EST) and 05:00 in summer (EDT): schedules
      # in UTC do not follow daylight saving time. Either hour is fine for a daily feed check.
      expression = "cron(0 9 * * ? *)"
      function   = aws_lambda_function.static.arn
      input      = "{}"
    }
  }
}

resource "aws_scheduler_schedule" "pipeline" {
  for_each = local.pipeline_schedules

  name                = "headway-${each.key}"
  schedule_expression = each.value.expression

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = each.value.function
    role_arn = aws_iam_role.scheduler.arn
    input    = each.value.input

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}
