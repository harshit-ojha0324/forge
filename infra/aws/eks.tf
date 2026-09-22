module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.25"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  # Public API endpoint so kubectl works from a laptop — restrict it to
  # your IP via public_access_cidrs. Nodes stay private either way.
  endpoint_public_access       = true
  endpoint_public_access_cidrs = var.public_access_cidrs

  # The identity running terraform becomes cluster admin through an EKS
  # access entry (API auth mode — no aws-auth ConfigMap to hand-edit).
  enable_cluster_creator_admin_permissions = true

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  addons = {
    coredns    = {}
    kube-proxy = {}
    vpc-cni = {
      before_compute = true
    }
    # Pod Identity: how Karpenter's controller gets AWS credentials —
    # the EKS analogue of GKE workload identity, no key files.
    eks-pod-identity-agent = {
      before_compute = true
    }
  }

  # CPU pool: gateway, redis, mocks, ArgoCD, kube-prometheus-stack and the
  # Karpenter controller. A managed node group, not Karpenter, so the
  # thing that provisions nodes never runs on a node it provisioned.
  #
  # NOTE: unlike GKE, a managed node group does not autoscale by itself —
  # min/max only bound manual scaling. Karpenter handles the elastic part
  # (the GPU); this pool is sized by hand.
  eks_managed_node_groups = {
    services = {
      ami_type       = "AL2023_x86_64_STANDARD"
      capacity_type  = "SPOT"
      instance_types = var.services_instance_types

      min_size     = var.services_node_count_min
      desired_size = var.services_node_count_desired
      max_size     = var.services_node_count_max

      # (disk_size is silently ignored with the module's custom launch
      # template; the root volume must be set as a block device.)
      block_device_mappings = {
        root = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = 50
            volume_type           = "gp3"
            encrypted             = true
            delete_on_termination = true
          }
        }
      }

      labels = {
        pool                      = "services"
        "karpenter.sh/controller" = "true"
      }
    }
  }

  # Karpenter-launched nodes join this security group by tag.
  node_security_group_tags = {
    "karpenter.sh/discovery" = var.cluster_name
  }
}
