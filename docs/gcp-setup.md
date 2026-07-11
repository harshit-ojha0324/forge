# GCP setup — from zero to `terraform apply`

One-time setup, ~30 minutes of work plus a wait for GPU quota approval.
Do the quota request (§4) on day one — approval can take up to a day.

## 1. Project + billing

```bash
gcloud auth login
gcloud projects create forge-platform-<yourname> --name="Forge Platform"
gcloud config set project forge-platform-<yourname>

# link billing (needs a billing account with a card attached)
gcloud billing accounts list
gcloud billing projects link forge-platform-<yourname> --billing-account=<BILLING_ACCOUNT_ID>
```

Free-trial credits note: some free-trial accounts cannot request GPU
quota until upgraded to a paid account (your card is still not charged
beyond credits). If §4 is rejected, upgrade the account and retry.

## 2. Budget alert (do this before anything else)

Console → Billing → Budgets & alerts → Create budget: $25, alerts at
50/90/100%. This is the seatbelt for the whole project.

## 3. Enable APIs

```bash
gcloud services enable \
  container.googleapis.com \
  compute.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  cloudbilling.googleapis.com
```

## 4. GPU quota (the long pole — request on day 1)

Console → IAM & Admin → Quotas → filter:
- `Preemptible NVIDIA T4 GPUs` (region **us-central1**) → request **1**
- also check `GPUS_ALL_REGIONS` → request **1** if it shows 0

Justification text that works: "Learning project: single spot T4 for
self-hosted LLM inference on GKE, scaled to zero when idle."

## 5. Terraform state bucket (optional but recommended)

```bash
gsutil mb -l us-central1 gs://forge-platform-<yourname>-tfstate
gsutil versioning set on gs://forge-platform-<yourname>-tfstate
```

Then uncomment the `backend "gcs"` block in `infra/terraform/versions.tf`
with this bucket name.

## 6. Provision

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # set project_id
terraform init
terraform plan     # READ the plan — it's the interview answer sheet
terraform apply    # ~10 min, mostly the cluster

$(terraform output -raw get_credentials)       # point kubectl at the cluster
kubectl get nodes                              # 1 services node, no GPU node
```

## 7. Push images to Artifact Registry

```bash
gcloud auth configure-docker us-central1-docker.pkg.dev
REG=$(terraform output -raw registry)
for svc in gateway mock-llm agent-demo; do
  docker build -t "$REG/$svc:v1" "services/$svc" --platform linux/amd64
  docker push "$REG/$svc:v1"
done
```

(Apple Silicon note: `--platform linux/amd64` matters — the cluster nodes
are x86.)

## 8. Install ArgoCD and hand it the keys

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# update repoURL in deploy/argocd/*.yaml to your fork, set image
# repository/tag in deploy/helm/*/values.yaml to the registry above,
# commit, push, then the ONLY manual apply of the project:
kubectl apply -n argocd -f deploy/argocd/root-app.yaml

# watch it converge
kubectl -n argocd get applications
```

From here on: change YAML → git push → ArgoCD syncs. That's the deploy
pipeline.

## 9. CI publishing (optional, stage 5)

The GitHub Actions `publish` job uses keyless auth (workload identity
federation). Set up a WIF pool + provider bound to your repo, create a
`forge-ci` service account with `roles/artifactregistry.writer`, then set
repo variables `GCP_PUBLISH=true`, `GCP_PROJECT_ID`, `GCP_REGION` and
secrets `GCP_WIF_PROVIDER`, `GCP_CI_SERVICE_ACCOUNT`. Google's
`google-github-actions/auth` README has the four commands.

## 10. Teardown (end of every session)

```bash
kubectl delete -n argocd application vllm   # GPU pool back to 0 first
terraform destroy                            # everything else
```
