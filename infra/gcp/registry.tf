resource "google_artifact_registry_repository" "forge" {
  repository_id = "forge"
  location      = var.region
  format        = "DOCKER"
  description   = "Forge platform images (gateway, mock-llm, agents)"
}
