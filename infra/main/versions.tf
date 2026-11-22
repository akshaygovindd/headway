terraform {
  required_version = ">= 1.10"

  # Partial configuration: supply the values with `-backend-config=backend.hcl`.
  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      project = "headway"
      env     = "prod"
    }
  }
}

data "aws_caller_identity" "current" {}
