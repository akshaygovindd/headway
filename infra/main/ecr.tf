resource "aws_ecr_repository" "pipeline" {
  # The training image exists only with Release 3, the API image only with Release 4.
  for_each = toset(concat(["curate", "transform"], var.enable_r3 ? ["train"] : [], var.enable_r4 ? ["api"] : []))

  name                 = "headway-${each.key}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "pipeline" {
  for_each   = aws_ecr_repository.pipeline
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}
