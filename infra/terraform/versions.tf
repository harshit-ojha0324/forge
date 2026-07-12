terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
  }

  # Remote state: versioned GCS bucket (docs/gcp-setup.md §5). State
  # contains resource attributes — the bucket is private to the project.
  backend "gcs" {
    bucket = "forge-harshit-26-tfstate"
    prefix = "forge"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
