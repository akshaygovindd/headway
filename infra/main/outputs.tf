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
