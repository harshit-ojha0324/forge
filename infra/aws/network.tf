data "aws_availability_zones" "available" {
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"] # skip Local Zones
  }
}

locals {
  vpc_cidr = "10.10.0.0/16"
  # Three AZs: EKS needs two, and every extra AZ is another g4dn spot
  # pool Karpenter can fall back to when one is out of capacity.
  azs = slice(data.aws_availability_zones.available.names, 0, 3)
}

# Private nodes, same posture as the GKE build: no public IPs on any node;
# egress through one NAT gateway. Public subnets hold only the NAT.
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.7"

  name = "${var.cluster_name}-vpc"
  cidr = local.vpc_cidr
  azs  = local.azs

  private_subnets = [for i, _ in local.azs : cidrsubnet(local.vpc_cidr, 4, i)]      # /20s: pods get VPC IPs
  public_subnets  = [for i, _ in local.azs : cidrsubnet(local.vpc_cidr, 8, i + 48)] # /24s: NAT only

  enable_nat_gateway = true
  single_nat_gateway = true # one NAT (~$1/day) instead of one per AZ — lab trade-off

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = 1
    "karpenter.sh/discovery"          = var.cluster_name # Karpenter launches GPU nodes here
  }
  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1
  }
}

# ECR stores image layers in S3. A gateway endpoint (free) keeps those
# pulls off the NAT gateway, which bills $0.045 per GB processed.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids
}
