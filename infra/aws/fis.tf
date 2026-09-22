# The AWS version of the GKE preemption drill (simulate-maintenance-event):
# AWS Fault Injection Service sends a REAL spot interruption notice to the
# GPU node, exactly as EC2 would when reclaiming capacity. Karpenter sees
# it on the SQS queue, cordons + drains the node, and launches a
# replacement while the gateway's breaker fails over to the fallback.
#
#   aws fis start-experiment --experiment-template-id $(terraform output -raw fis_spot_drill_template_id)
#
# Costs ~$0.10 per action-minute; the template does nothing until started.
data "aws_iam_policy_document" "fis_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["fis.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fis" {
  name               = "${var.cluster_name}-fis-spot-drill"
  assume_role_policy = data.aws_iam_policy_document.fis_trust.json
}

data "aws_iam_policy_document" "fis" {
  statement {
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
  statement {
    actions   = ["ec2:SendSpotInstanceInterruptions"]
    resources = ["arn:aws:ec2:${var.region}:*:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/karpenter.sh/nodepool"
      values   = ["gpu"]
    }
  }
}

resource "aws_iam_role_policy" "fis" {
  name   = "spot-interrupt-gpu"
  role   = aws_iam_role.fis.id
  policy = data.aws_iam_policy_document.fis.json
}

resource "aws_fis_experiment_template" "gpu_spot_interruption" {
  description = "Forge drill: spot-interrupt the Karpenter GPU node (2-minute notice)"
  role_arn    = aws_iam_role.fis.arn

  stop_condition {
    source = "none"
  }

  action {
    name      = "interrupt-gpu-node"
    action_id = "aws:ec2:send-spot-instance-interruptions"

    parameter {
      key   = "durationBeforeInterruption"
      value = "PT2M" # the real spot notice window
    }

    target {
      key   = "SpotInstances"
      value = "gpu-nodes"
    }
  }

  target {
    name           = "gpu-nodes"
    resource_type  = "aws:ec2:spot-instance"
    selection_mode = "ALL"

    resource_tag {
      key   = "karpenter.sh/nodepool"
      value = "gpu"
    }

    filter {
      path   = "State.Name"
      values = ["running"]
    }
  }

  tags = {
    Name = "${var.cluster_name}-gpu-spot-drill"
  }
}
