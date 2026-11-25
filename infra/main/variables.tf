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

variable "transform_duckdb_memory_limit" {
  description = "DuckDB memory_limit inside the transform Lambda (account cap is 3008 MB, ADR-024)."
  type        = string
  default     = "2200MB"
}

variable "transform_duckdb_threads" {
  description = "DuckDB and dbt threads inside the transform Lambda."
  type        = number
  default     = 2
}

variable "transform_duckdb_max_temp" {
  description = "Spill budget under /tmp for the transform Lambda; must fit its ephemeral storage."
  type        = string
  default     = "8GB"
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

variable "enable_r4" {
  description = "Deploy the Release 4 public read-only metric API (Lambda behind an HTTP API). Needs a Lambda concurrency limit of at least 100: see api_confirmed_concurrency_limit."
  type        = bool
  default     = false

  validation {
    condition     = !var.enable_r4 || var.api_image_uri != ""
    error_message = "enable_r4 deploys the API Lambda: set api_image_uri (make images-push ENABLE_R4=true prints it)."
  }

  validation {
    condition     = !var.enable_r4 || var.api_confirmed_concurrency_limit >= 100 + var.api_reserved_concurrency
    error_message = "enable_r4 is blocked until the account's Lambda concurrent executions limit is at least 100 plus api_reserved_concurrency (new accounts start at 10; Lambda keeps 100 unreserved, so a reservation cannot be made, and one that could would starve the pipeline). Check: aws lambda get-account-settings --query AccountLimit.ConcurrentExecutions. Request 1000 under Service Quotas > Lambda > Concurrent executions, then set api_confirmed_concurrency_limit to the value the command prints."
  }
}

variable "api_confirmed_concurrency_limit" {
  description = "The account's Lambda concurrent executions limit as printed by `aws lambda get-account-settings --query AccountLimit.ConcurrentExecutions`. enable_r4 requires at least 100 plus api_reserved_concurrency (request 1000). A statement you make, not a lookup: the plan cannot read the quota."
  type        = number
  default     = 0
}

variable "api_image_uri" {
  description = "ECR image URI for the public API Lambda (printed by `make images-push ENABLE_R4=true`). Needed when enable_r4 = true."
  type        = string
  default     = ""
}

variable "api_reserved_concurrency" {
  description = "Reserved concurrent executions of the API Lambda: its ceiling, and the capacity it takes away from the pipeline functions."
  type        = number
  default     = 3

  validation {
    condition     = var.api_reserved_concurrency >= 1 && var.api_reserved_concurrency <= 50
    error_message = "api_reserved_concurrency must be between 1 and 50."
  }
}

variable "api_cors_origins" {
  description = "Browser origins the API allows (CORS), comma separated. Placeholder until the Vercel domain exists; never a wildcard."
  type        = string
  default     = "https://headway.example.vercel.app"

  validation {
    condition     = !strcontains(var.api_cors_origins, "*")
    error_message = "api_cors_origins must list explicit origins, not a wildcard."
  }
}

variable "api_throttle_rate" {
  description = "API Gateway steady-state requests per second for the whole API."
  type        = number
  default     = 10
}

variable "api_throttle_burst" {
  description = "API Gateway burst limit (requests) for the whole API."
  type        = number
  default     = 20
}
