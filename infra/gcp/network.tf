# Custom-mode VPC: no auto subnets, so nothing exists that we didn't declare.
resource "google_compute_network" "forge" {
  name                    = "${var.cluster_name}-vpc"
  auto_create_subnetworks = false
}

# VPC-native GKE needs secondary ranges: pods and services get real VPC IPs
# (alias IPs) instead of routed overlay space.
resource "google_compute_subnetwork" "gke" {
  name          = "${var.cluster_name}-gke"
  network       = google_compute_network.forge.id
  region        = var.region
  ip_cidr_range = "10.10.0.0/20" # nodes

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }

  private_ip_google_access = true # image pulls & API access without public IPs
}

# Spot nodes have no external IPs; NAT gives them outbound internet
# (model downloads from Hugging Face, OS packages).
resource "google_compute_router" "forge" {
  name    = "${var.cluster_name}-router"
  network = google_compute_network.forge.id
  region  = var.region
}

resource "google_compute_router_nat" "forge" {
  name                               = "${var.cluster_name}-nat"
  router                             = google_compute_router.forge.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}
