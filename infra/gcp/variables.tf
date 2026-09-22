variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "Region for the VPC and Artifact Registry"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Zone for the (zonal) GKE cluster — zonal control planes are covered by GKE's free-tier credit"
  type        = string
  default     = "us-central1-a"
}

variable "cluster_name" {
  type    = string
  default = "forge"
}

variable "services_machine_type" {
  description = "Machine type for the CPU node pool running gateway/agents/observability"
  type        = string
  default     = "e2-standard-2"
}

variable "services_node_count_min" {
  type    = number
  default = 1
}

variable "services_node_count_max" {
  type    = number
  default = 3
}

variable "gpu_machine_type" {
  description = "Host machine for the T4 node (T4 attaches to N1 hosts)"
  type        = string
  default     = "n1-standard-4"
}

variable "gpu_type" {
  type    = string
  default = "nvidia-tesla-t4"
}

variable "gpu_node_count_max" {
  description = "GPU pool autoscales 0..max; keep 0 when not demoing so a spot T4 costs nothing"
  type        = number
  default     = 1
}
