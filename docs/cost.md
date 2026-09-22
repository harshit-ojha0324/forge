# Cost breakdown

*The design goal: idle cost near zero, demo cost measured in cents.
Prices are list prices as of 2026; check current rates.*

## AWS / EKS (current target, us-east-2)

The big difference from GKE: **the EKS control plane is never free**, so
an idle AWS stack costs roughly 3× the GKE one. Teardown discipline
matters more here, not less.

| Item | Spec | ~$/hr | ~$/day |
|---|---|---|---|
| EKS control plane | 1.35, **standard** support | $0.10 | $2.40 |
| NAT gateway + its public IPv4 | one, shared by 3 AZs | $0.05 + $0.045/GB | $1.20 |
| services node group | 2 × t3.large-class **spot** | ~$0.06 | ~$1.45 |
| EBS | 2 × 50 GB gp3 | ~$0.01 | $0.27 |
| ECR | few GB, 20 images/repo kept | — | < $0.05 |
| **idle total** | | **~$0.22** | **~$5.30** |

⚠ Two traps specific to EKS:
- **Extended support** bills the control plane at **$0.60/hr** (6×)
  once a Kubernetes version leaves standard support (~14 months after
  release). Bump `kubernetes_version` before that happens.
- **Orphaned Karpenter instances**: GPU nodes are launched by Karpenter,
  not Terraform, so `terraform destroy` doesn't know about them. Follow
  the teardown order in [aws-setup.md §10](aws-setup.md).

GPU on (demos only):

| Item | Spec | ~$/hr |
|---|---|---|
| g4dn.xlarge **spot** | 4 vCPU / 16 GB / 1 × T4 | ~$0.16–0.25 (on-demand $0.53) |
| 100 GB gp3 root | image + model weights | ~$0.01 |
| NAT data | ~16 GB image + weights per fresh node, from Docker Hub/HF | ~$0.70 per node boot |
| FIS spot-interruption drill | 2-minute action | ~$0.20 per run |

A 2-hour GPU session on top of the idle stack ≈ **$1.50–2.00**.
Pause the whole thing between sessions: rebuild is ~20 min from S3 state.

## GKE (archived July–Aug 2026 build)

### Steady state (what runs 24/7 if you leave it up)

| Item | Spec | ~$/hr | ~$/mo |
|---|---|---|---|
| GKE control plane | zonal | $0.10 | covered by GKE free-tier credit ($74.40/mo, one zonal cluster) |
| services pool | 1–2 × e2-standard-2 **spot** | ~$0.02/node | $15–30 |
| gpu-t4 pool | **scaled to zero** | $0 | $0 |
| Cloud NAT | gateway + data | ~$0.045 + data | ~$32/mo if left up ⚠ |
| Artifact Registry | few GB | — | < $1 |

⚠ Cloud NAT is the sneaky one, not the GPU. Two options: tear down the
whole stack between sessions (`terraform destroy` — everything is code,
rebuild is ~15 min) or accept it for the active week and destroy after.

### When the GPU is on (demos and dev sessions only)

| Item | Spec | ~$/hr |
|---|---|---|
| n1-standard-4 (spot) | 4 vCPU / 15 GB | ~$0.04 |
| T4 GPU (spot) | 16 GB | ~$0.11–0.16 |
| **total GPU-on cost** | | **~$0.15–0.20/hr** |

A 2-hour demo session ≈ **35–40¢**. The pool autoscales 0→1 when the
vLLM pod schedules and back to 0 when the app is deleted — turning the
GPU on/off is an ArgoCD sync/delete, not a console operation.

## Fallback (Gemini) cost

gemini-2.0-flash-class pricing is fractions of a cent per request at this
project's token sizes. During a failover incident the per-tenant token
dashboard quantifies exactly what shifted to the paid API.

## Realistic project total

- Local development (stage 1): **$0** — the entire platform runs on
  docker-compose.
- Cloud stages (2–5) with disciplined teardown, ~10–15 hours of cluster
  time + a few GPU hours: **$10–25 total**.
- Leaving everything up for a month instead: ~$80–100 — don't.

## Cost discipline checklist

- [ ] `terraform destroy` at the end of every session (state is in S3; on AWS follow aws-setup.md §10 order)
- [ ] vLLM app deleted (GPU pool at 0) whenever not actively demoing
- [ ] Billing budget alert at $25 and $50 (aws-setup.md §1)
- [ ] Spot everywhere; nothing in this lab justifies on-demand
