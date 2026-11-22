output "tfstate_bucket" {
  description = "Bucket that holds the main stack's Terraform state."
  value       = aws_s3_bucket.tfstate.bucket
}
