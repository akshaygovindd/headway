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

variable "vehicle_polls" {
  description = "Vehicle position polls per minute: 1 is one poll per minute, 4 is 15 s sampling (ADR-014)."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 4], var.vehicle_polls)
    error_message = "vehicle_polls must be 1 or 4."
  }
}

variable "enable_r2" {
  description = "Build the Release 2 dbt models (tag r2) in the transform Lambda."
  type        = bool
  default     = false
}

variable "enable_r3" {
  description = "Build the Release 3 dbt models (tag r3: forecast examples, training gate) in the transform Lambda. Needs enable_r2."
  type        = bool
  default     = false

  validation {
    condition     = !var.enable_r3 || var.enable_r2
    error_message = "enable_r3 reads Release 2 models: set enable_r2 = true as well."
  }
}

variable "train_image_uri" {
  description = "ECR image URI for the weekly forecast training Lambda (printed by `make images-push`). Needed when enable_r3 = true."
  type        = string
  default     = ""

  validation {
    condition     = !var.enable_r3 || var.train_image_uri != ""
    error_message = "enable_r3 deploys the training Lambda: set train_image_uri (make images-push prints it)."
  }
}
