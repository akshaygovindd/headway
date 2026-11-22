resource "aws_budgets_budget" "monthly" {
  name         = "headway-monthly"
  budget_type  = "COST"
  limit_amount = var.budget_limit_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Requires the `project` cost allocation tag to be activated in Billing.
  cost_filter {
    name   = "TagKeyValue"
    values = ["user:project$headway"]
  }

  dynamic "notification" {
    for_each = [5, 10]

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "ABSOLUTE_VALUE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.budget_email]
    }
  }
}
