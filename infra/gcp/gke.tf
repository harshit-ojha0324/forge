resource "google_container_cluster" "forge" {
  name     = var.cluster_name
  location = var.zone # zonal: one control plane, covered by the free-tier credit

  network    = google_compute_network.forge.id
  subnetwork = google_compute_subnetwork.gke.id

  # We manage node pools explicitly; the default pool is deleted on create.
  remove_default_node_pool = true
  initial_node_count       = 1

  networking_mode = "VPC_NATIVE"
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Private nodes: no external IPs; egress goes through Cloud NAT
  # (network.tf) and image pulls via Private Google Access. The control
  # plane keeps a public endpoint so kubectl works from a laptop.
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  release_channel {
    channel = "REGULAR"
  }

  deletion_protection = false # this is a lab platform; terraform destroy must work
}

# CPU pool: gateway, agents, Prometheus/Grafana, ArgoCD, mocks.
resource "google_container_node_pool" "services" {
  name     = "services"
  cluster  = google_container_cluster.forge.id
  location = var.zone

  autoscaling {
    min_node_count = var.services_node_count_min
    max_node_count = var.services_node_count_max
  }
  initial_node_count = var.services_node_count_min

  node_config {
    machine_type    = var.services_machine_type
    spot            = true
    disk_size_gb    = 50
    service_account = google_service_account.gke_nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels = {
      pool = "services"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

# GPU pool: one spot T4 for vLLM, scaled to zero when idle. GKE installs
# the NVIDIA driver via gpu_driver_installation_config; the taint keeps
# non-GPU workloads off the expensive node so autoscaling can reach zero.
resource "google_container_node_pool" "gpu_t4" {
  name     = "gpu-t4"
  cluster  = google_container_cluster.forge.id
  location = var.zone

  autoscaling {
    min_node_count = 0
    max_node_count = var.gpu_node_count_max
  }
  initial_node_count = 0

  node_config {
    machine_type    = var.gpu_machine_type
    spot            = true
    disk_size_gb    = 100
    service_account = google_service_account.gke_nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    guest_accelerator {
      type  = var.gpu_type
      count = 1
      gpu_driver_installation_config {
        gpu_driver_version = "DEFAULT"
      }
    }

    labels = {
      pool = "gpu"
    }

    taint {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NO_SCHEDULE"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
