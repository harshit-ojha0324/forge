variable "region" {
  description = "AWS region. A 'new AWS experience' project can only create resources in its own assigned region (AWS Settings > project > Additional Info)."
  type        = string
  default     = "us-east-2"
}

variable "cluster_name" {
  type    = string
  default = "forge"
}

variable "kubernetes_version" {
  description = "Keep this inside EKS *standard* support — extended support bills $0.60/hr instead of $0.10/hr"
  type        = string
  default     = "1.35"
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the EKS API endpoint. Set to [\"<your-ip>/32\"]; the default is open (auth still required)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "services_instance_types" {
  description = "Spot candidates for the CPU pool. Large, not medium: the VPC CNI caps a t3.medium at 17 pods, and ArgoCD + kube-prometheus-stack alone exceed that."
  type        = list(string)
  default     = ["t3.large", "t3a.large", "m5.large", "m5a.large"]
}

variable "services_node_count_min" {
  type    = number
  default = 1
}

variable "services_node_count_desired" {
  type    = number
  default = 2
}

variable "services_node_count_max" {
  type    = number
  default = 3
}

variable "karpenter_version" {
  description = "Karpenter chart version; must support kubernetes_version (Karpenter compatibility matrix: 1.35 needs >= 1.9)"
  type        = string
  default     = "1.14.1"
}

variable "github_repo" {
  description = "owner/name of the repo whose main branch may push images"
  type        = string
  default     = "harshit-ojha0324/forge"
}

variable "create_github_oidc_provider" {
  description = "An account holds ONE GitHub OIDC provider; set false if another project already created it"
  type        = bool
  default     = true
}
