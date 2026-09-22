# Karpenter's AWS side: controller role (via Pod Identity), node role +
# EKS access entry, and the interruption pipeline — EventBridge rules
# for spot interruption warnings / rebalance / instance state changes,
# fanned into an SQS queue the controller watches. That queue is what
# turns a 2-minute spot notice into a cordon + drain + replacement,
# instead of the node just vanishing.
module "karpenter" {
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "~> 21.25"

  cluster_name = module.eks.cluster_name

  # Fixed name: deploy/karpenter/gpu-nodepool.yaml references it.
  node_iam_role_use_name_prefix = false
  node_iam_role_name            = "${var.cluster_name}-karpenter-node"

  create_pod_identity_association = true

  node_iam_role_additional_policies = {
    # Shell onto GPU nodes via SSM Session Manager — no SSH, no bastion.
    AmazonSSMManagedInstanceCore = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  }
}

resource "helm_release" "karpenter" {
  namespace  = "kube-system"
  name       = "karpenter"
  repository = "oci://public.ecr.aws/karpenter"
  chart      = "karpenter"
  version    = var.karpenter_version
  wait       = false

  values = [
    <<-EOT
    replicas: 1 # lab-grade; 2 for HA across the services nodes
    nodeSelector:
      karpenter.sh/controller: "true"
    # Karpenter may start before CoreDNS is schedulable; resolve via the node.
    dnsPolicy: Default
    settings:
      clusterName: ${module.eks.cluster_name}
      clusterEndpoint: ${module.eks.cluster_endpoint}
      interruptionQueue: ${module.karpenter.queue_name}
    EOT
  ]

  depends_on = [module.eks]
}
