output "cluster_name" {
  value = module.eks.cluster_name
}

output "get_credentials" {
  description = "Run this to point kubectl at the new cluster"
  value       = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region}"
}

output "registry" {
  description = "Image prefix for pushes and Helm values: <registry>/<svc>"
  value       = split("/", aws_ecr_repository.forge["gateway"].repository_url)[0]
}

output "image_prefix" {
  value = "${split("/", aws_ecr_repository.forge["gateway"].repository_url)[0]}/${var.cluster_name}"
}

output "ci_role_arn" {
  description = "GitHub secret AWS_CI_ROLE_ARN"
  value       = aws_iam_role.ci.arn
}

output "karpenter_interruption_queue" {
  value = module.karpenter.queue_name
}

output "fis_spot_drill_template_id" {
  value = aws_fis_experiment_template.gpu_spot_interruption.id
}
