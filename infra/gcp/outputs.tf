output "cluster_name" {
  value = google_container_cluster.forge.name
}

output "get_credentials" {
  description = "Run this to point kubectl at the new cluster"
  value       = "gcloud container clusters get-credentials ${google_container_cluster.forge.name} --zone ${var.zone} --project ${var.project_id}"
}

output "registry" {
  description = "Docker image prefix for pushes"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.forge.repository_id}"
}

output "gateway_service_account" {
  value = google_service_account.gateway.email
}
