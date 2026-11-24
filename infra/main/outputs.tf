output "raw_bucket" {
  value = aws_s3_bucket.raw.bucket
}

output "public_bucket" {
  value = aws_s3_bucket.public.bucket
}

output "function_name" {
  value = aws_lambda_function.ingest.function_name
}

output "public_snapshot_url" {
  value = "https://${aws_s3_bucket.public.bucket_regional_domain_name}/${local.live_prefix}/vehicles_latest.json"
}

output "curate_repository_url" {
  value = aws_ecr_repository.pipeline["curate"].repository_url
}

output "transform_repository_url" {
  value = aws_ecr_repository.pipeline["transform"].repository_url
}

output "gold_url" {
  value = "https://${aws_s3_bucket.public.bucket_regional_domain_name}/${local.gold_prefix}"
}

output "train_function_name" {
  description = "The weekly forecast training Lambda (empty unless enable_r3)."
  value       = one(aws_lambda_function.train[*].function_name)
}
