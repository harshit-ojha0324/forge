terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
  }

  # Remote state: uncomment after creating the bucket (docs/gcp-setup.md).
  # backend "gcs" {
  #   bucket = "<project-id>-forge-tfstate"
  #   prefix = "forge"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
