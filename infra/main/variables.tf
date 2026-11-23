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
  default     = "15"
}

variable "curate_image_uri" {
  description = "ECR image URI for the curate and static Lambdas (printed by `make images-push`)."
  type        = string
}

variable "transform_image_uri" {
  description = "ECR image URI for the transform Lambda (printed by `make images-push`)."
  type        = string
}

variable "lookback_days" {
  description = "Days of realtime history the transform Lambda reads."
  type        = number
  default     = 7
}
