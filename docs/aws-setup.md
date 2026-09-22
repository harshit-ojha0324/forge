# AWS setup — from zero to a running EKS platform

One-time setup, ~45 minutes of work plus a wait for GPU quota approval.
Do the quota request (§3) on day one: new accounts often start at **0**
G-instance vCPUs, and approval can take hours to days.

The GKE build this replaces is archived in [gcp-setup.md](gcp-setup.md)
(`infra/gcp/`); every drill number in the README before the AWS section
was measured there.

| GKE concept | EKS equivalent here |
|---|---|
| zonal GKE (free-tier control plane) | EKS 1.35 — **$0.10/hr, no free tier** |
| spot node pools + cluster autoscaler | managed `services` node group (spot, fixed size) + **Karpenter** for the GPU |
| GKE-managed NVIDIA driver | AL2023 NVIDIA AMI (Karpenter picks it) + `nvidia-device-plugin` app |
| Cloud NAT | one NAT gateway + free S3 gateway endpoint for ECR layer pulls |
| Artifact Registry (1 repo) | 3 ECR repositories, lifecycle-pruned |
| Workload Identity | EKS Pod Identity (Karpenter's controller) |
| WIF keyless CI | GitHub OIDC provider + push-only IAM role |
| GCS state bucket | S3 bucket with native lockfile (`use_lockfile`) |
| `simulate-maintenance-event` drill | **AWS FIS** real spot interruption notice |

## 0a. If your account is a "new AWS experience" project

Accounts created with social sign-in are *projects*: AWS attaches service
control policies you cannot edit, on **both** the Free and Paid plans.
Four of them block this design outright:

| Denied by the project SCP | What it breaks here |
|---|---|
| `ec2:RequestSpotInstances`, `RunInstances` with `InstanceMarketType=spot` | every node in this build is spot |
| `ec2:CreateFleet` | the only API Karpenter launches nodes with |
| `iam:*Provider*` | the GitHub OIDC provider (keyless CI) and the EKS module's IRSA provider |
| `fis:*` on the Free plan | the spot-interruption drill (§9) |

Check yours with `aws freetier get-account-plan-state` and the
[project SCP reference](https://docs.aws.amazon.com/accounts/latest/reference/scps-and-rcps-for-projects.html).

To run this platform as written: upgrade to the Paid plan, then
**Activate advanced AWS features** — you become the admin of the AWS
Organization holding the project and can remove those policies. The
alternative is an on-demand rebuild (no spot, Cluster Autoscaler instead
of Karpenter, CI keys instead of OIDC), which costs more and drops the
parts worth defending in an interview.

Projects are also single-region: everything must live in the project's
assigned region (this repo defaults to `us-east-2`).

## 0b. A dedicated identity (not another project's keys)

Use an identity that exists for Forge only. On a project account,
`aws login` gives you a browser-based role session (12 hours, renewable
for 90 days) — no access keys to leak:

```bash
aws configure set region us-east-2 --profile forge
aws login --profile forge
aws sts get-caller-identity --profile forge   # confirm the account before every apply
```

On a classic account, use an IAM Identity Center user with
`AdministratorAccess` and `aws configure sso --profile forge` instead.

`terraform apply` makes that identity the EKS cluster admin (access entry).

## 1. Budget alert (before anything else)

Console → Billing → Budgets → Create budget: **$25**, alerts at 50/90/100%.
EKS bills ~$5/day just for existing (see [cost.md](cost.md)); this is the
seatbelt.

## 2. Spot service-linked role

Karpenter launches spot instances through EC2 Fleet, which needs this
role. Fresh accounts don't have it, and the failure surfaces much later
as an obscure `AuthFailure.ServiceLinkedRoleCreationNotPermitted` on the
first GPU launch.

```bash
aws iam create-service-linked-role --aws-service-name spot.amazonaws.com
# "has been taken in this account" = already exists = fine
```

## 3. GPU quota (the long pole; request on day 1)

g4dn.xlarge = 4 vCPUs. Quotas are counted in vCPUs, per region.

```bash
# All G and VT Spot Instance Requests (in your project region)
aws service-quotas get-service-quota --service-code ec2 --quota-code L-3819A6DF \
  --query 'Quota.Value'
aws service-quotas request-service-quota-increase --service-code ec2 \
  --quota-code L-3819A6DF --desired-value 8
```

8 leaves headroom for the g4dn.2xlarge fallback in the NodePool. If you
may ever allow on-demand GPUs, also raise *Running On-Demand G and VT
instances* (`L-DB2E81BA`).

## 4. Terraform state bucket

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=forge-$ACCOUNT-tfstate
aws s3api create-bucket --bucket $BUCKET --region us-east-2 \
  --create-bucket-configuration LocationConstraint=us-east-2   # required outside us-east-1
aws s3api put-bucket-versioning --bucket $BUCKET --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket $BUCKET \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

cd infra/aws
sed "s/<account-id>/$ACCOUNT/" backend.hcl.example > backend.hcl
```

Locking is S3's native lockfile (Terraform ≥ 1.10): no DynamoDB table.

## 5. Provision

```bash
cd infra/aws
cp terraform.tfvars.example terraform.tfvars   # set public_access_cidrs to your IP/32
terraform init -backend-config=backend.hcl
terraform plan     # READ it — it's the interview answer sheet
terraform apply    # ~15–20 min, mostly the EKS control plane

$(terraform output -raw get_credentials)
kubectl get nodes                          # 2 services nodes, no GPU node
kubectl -n kube-system get pods -l app.kubernetes.io/name=karpenter
```

(Terraform lives at `.tools/terraform` in this repo, not on PATH.)

## 6. Keyless CI → ECR, and the first images

```bash
gh variable set AWS_PUBLISH -b true
gh variable set AWS_REGION -b us-east-2
gh secret set AWS_CI_ROLE_ARN -b "$(terraform output -raw ci_role_arn)"
gh variable set GCP_PUBLISH -b false      # retire the GKE publish path
```

Then push to main. CI runs tests → evals → `publish` (build + push to
ECR, tagged with the short sha) → `promote` (commits the sha tag and the
real ECR path into `deploy/helm/*/values.yaml`). Wait for that
`deploy: images <sha>` commit before §7: it is what replaces the
`ACCOUNT_ID` placeholder in the values files.

The trust policy only accepts tokens whose subject is
`repo:harshit-ojha0324/forge:ref:refs/heads/main`: PR builds and forks
cannot assume the role, and the role can only push to the three Forge
repositories.

## 7. Out-of-band secrets, then ArgoCD

```bash
kubectl create namespace forge
kubectl create namespace monitoring

# tenant keys (git-ignored file); the gateway hot-reloads this Secret,
# so later rotations are just `kubectl apply` + ~1 minute
kubectl -n forge create secret generic forge-tenants-prod \
  --from-file=tenants.yaml=.secrets/tenants-prod.yaml

kubectl -n monitoring create secret generic grafana-admin \
  --from-literal=admin-user=admin \
  --from-literal=admin-password="$(openssl rand -base64 24)"

kubectl create namespace argocd
kubectl apply -n argocd --server-side -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl apply -n argocd -f deploy/argocd/root-app.yaml   # the ONLY manual apply
kubectl -n argocd get applications -w
```

`vllm` and `dcgm-exporter` stay manual-sync on purpose: syncing them is
what summons (and bills) a GPU.

## 8. GPU day

```bash
kubectl -n argocd patch application vllm --type merge \
  -p '{"operation":{"initiatedBy":{"username":"admin"},"sync":{"revision":"main"}}}'
# (or press Sync in the ArgoCD UI)
kubectl get nodeclaims -w                  # Karpenter picks a spot g4dn
kubectl -n kube-system logs -l app.kubernetes.io/name=karpenter -f
kubectl -n forge get pods -l app.kubernetes.io/name=vllm -w
```

Expect ~1–2 min for the node, then the ~10 GB vLLM image pull and model
download before readiness. The breaker's half-open probe brings traffic
home on its own.

## 9. The drill: a real spot interruption

Run the load generator **inside** the cluster (a port-forward tunnel
corrupts the numbers; see the README's GKE drill notes), then:

```bash
aws fis start-experiment \
  --experiment-template-id $(terraform -chdir=infra/aws output -raw fis_spot_drill_template_id)
```

FIS sends the GPU instance a genuine 2-minute spot interruption notice.
Karpenter reads it from its SQS queue, cordons and drains the node, and
launches a replacement; the gateway fails over to the fallback until the
new vLLM is ready. Measure 5xx (expect zero) exactly as in the GKE drill.

## 10. Teardown (end of every session)

**Order matters.** Karpenter's GPU instances are not in Terraform state.
Destroying the cluster first orphans them, and their network interfaces
then block the VPC from being deleted.

```bash
# 1. stop ArgoCD self-healing the NodePool back into existence
#    (deleting an Application without its finalizer leaves resources alone)
kubectl -n argocd delete application forge-root karpenter-nodepools vllm
# 2. Karpenter terminates every instance it launched
kubectl delete nodepools --all
kubectl get nodeclaims                     # wait until empty
# 3. now Terraform owns everything that's left
terraform -chdir=infra/aws destroy
```

The S3 state bucket survives, so the next `apply` rebuilds everything
from state.
