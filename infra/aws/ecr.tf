# One ECR repository per image (ECR has no multi-image "repo" like
# Artifact Registry): <account>.dkr.ecr.<region>.amazonaws.com/forge/<svc>
locals {
  images = toset(["gateway", "mock-llm", "agent-demo"])
}

resource "aws_ecr_repository" "forge" {
  for_each = local.images

  name                 = "${var.cluster_name}/${each.key}"
  image_tag_mutability = "MUTABLE" # CI moves :latest; deploys pin immutable sha tags
  force_delete         = true      # lab platform: terraform destroy must work

  image_scanning_configuration {
    scan_on_push = true
  }
}

# CI pushes an image per commit to main; keep the recent ones only.
resource "aws_ecr_lifecycle_policy" "forge" {
  for_each   = aws_ecr_repository.forge
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 20 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}
