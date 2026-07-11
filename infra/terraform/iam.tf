# Nodes run as a dedicated least-privilege service account instead of the
# project's default compute SA (which is Editor by default — the classic
# GKE security foot-gun).
resource "google_service_account" "gke_nodes" {
  account_id   = "${var.cluster_name}-gke-nodes"
  display_name = "Forge GKE node service account"
}

resource "google_project_iam_member" "node_roles" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/artifactregistry.reader",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.gke_nodes.email}"
}

# Workload identity: the gateway's Kubernetes ServiceAccount maps to this
# GCP SA, so pods call Google APIs (e.g. the real Gemini fallback) with
# short-lived tokens — no JSON key files mounted anywhere.
resource "google_service_account" "gateway" {
  account_id   = "${var.cluster_name}-gateway"
  display_name = "Forge gateway workload identity"
}

resource "google_service_account_iam_member" "gateway_workload_identity" {
  service_account_id = google_service_account.gateway.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[forge/gateway]"
}
