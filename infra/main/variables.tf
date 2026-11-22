variable "alert_email" {
  description = "Email address subscribed to the headway-alerts SNS topic."
  type        = string
}

variable "budget_email" {
  description = "Email address that receives AWS Budgets cost alerts."
  type        = string
}

variable "city_id" {
  description = "City whose realtime feeds are captured."
  type        = string
  default     = "boston"
}

variable "budget_limit_usd" {
  description = "Monthly cost budget in USD for resources tagged project=headway."
  type        = string
  default     = "10"
}
