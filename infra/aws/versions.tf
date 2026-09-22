terraform {
  required_version = ">= 1.10" # S3 native state locking (use_lockfile)

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.0"
    }
  }

  # Remote state: versioned S3 bucket, locked with S3's native lockfile
  # (no DynamoDB table). The bucket name is account-specific, so it is
  # passed at init time: terraform init -backend-config=backend.hcl
  # (docs/aws-setup.md §3).
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "forge"
      managed-by = "terraform"
    }
  }
}

# Karpenter is installed by Terraform (not ArgoCD) because its values are
# infrastructure outputs — IAM role, interruption queue — and because
# nothing ArgoCD deploys can schedule a GPU until it exists.
provider "helm" {
  kubernetes = {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
    exec = {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", module.eks.cluster_name, "--region", var.region]
    }
  }
}
