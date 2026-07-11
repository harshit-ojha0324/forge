# Stage 4 — Terraform & GKE: the real cloud deploy (2 days)

## Why this matters

"Provisioned the platform with Terraform" is only a resume line if you
can narrate `terraform plan` resource by resource. This is also the
stage where real money appears — cost discipline is part of the
curriculum, because cost stories are Solutions-Architect gold.

## Prerequisites

`docs/gcp-setup.md` §1–4 done (billing, budget alert, APIs, **T4 spot
quota requested on day one** — it can take 24h).

## Concepts

1. Terraform mental model: desired state → plan (diff) → apply; state
   file as the source of "what I own"; why state lives in GCS not git.
2. VPC anatomy: custom-mode VPC, subnets, secondary ranges (why GKE
   pods get real VPC IPs — VPC-native/alias IP), Cloud NAT for
   egress-only nodes.
3. GKE topology: zonal vs regional control plane ($$), node pools as
   the unit of machine-shape, autoscaling 0..N, taints/tolerations as
   the mechanism that keeps the GPU pool empty until wanted.
4. Workload identity: KSA→GSA federation; why key-file-in-a-Secret is
   the anti-pattern.
5. Spot economics: 60–90% discount for a 30s-preemption contract — and
   why stage 1's breaker is what makes spot *safe to use*.

Reading: every file in `infra/terraform/` (they're commented), then
`docs/cost.md`.

## Labs

### Lab 4.1 — Narrate the plan (do-together ★)
`terraform plan` and walk EVERY resource: what it is, why it exists,
what breaks without it, what it costs. The teacher plays customer and
interrupts with "why?" — this is a dress rehearsal for the interview.

### Lab 4.2 — Apply and verify (do-together)
`terraform apply`, get-credentials, then verify claims:
`kubectl get nodes` (1 services node, 0 GPU), pods have no external IP
but `kubectl run curl-test --image=curlimages/curl -it -- curl -sI
https://google.com` works (NAT). Find the node's service account in
the console and confirm it's NOT the default compute SA.

### Lab 4.3 — Rebuild one resource from memory (rebuild-solo ★)
`terraform destroy`, then delete `network.tf` and rewrite it from
memory (VPC, subnet + secondary ranges, router, NAT). `terraform plan`
against your version until it's clean, then apply. This is the
"could you do it again alone?" test.

### Lab 4.4 — Deploy the workloads (do-together)
docs/gcp-setup.md §7–8: push images (remember `--platform
linux/amd64`), install ArgoCD, apply root-app. Watch the platform
converge, port-forward the gateway, run `evals/run_evals.py` against
the CLUSTER. Same evals, same pass — that's the dev-prod parity story.

### Lab 4.5 — Cost audit (solo)
After a session: billing console → yesterday's spend by SKU. Write
three lines in PROGRESS.md: what cost money, what surprised you,
what you turned off.

## Break-it drill

Simulate spot preemption: `kubectl drain <services-node>
--ignore-daemonsets --delete-emptydir-data`. Watch pods reschedule
(pending → new node via autoscaler if needed), gateway replicas keep
serving (2 replicas — did requests drop?). Uncordon. Narrate what
Kubernetes did without you.

## Teach-back exam

★ Q1. What exactly is in the Terraform state file, and what goes wrong
if two people apply with local state?
**A:** The mapping from config resources to real cloud object IDs plus
last-known attributes. Two local states = both think they own the
world: duplicate resources, or destroy-plans against things the other
created. Remote state (GCS) + locking serializes applies and makes the
mapping shared. State also contains secrets → bucket needs access
control.

★ Q2. Why does the GPU node pool cost $0 right now, mechanically?
**A:** Autoscaling min=0 and nothing schedulable: the taint
(`nvidia.com/gpu:NoSchedule`) repels every pod that doesn't tolerate
it, and the only tolerating pod (vLLM) isn't deployed. No pending pod
→ autoscaler keeps 0 nodes → no VM exists → no bill. Deploying vLLM
creates a pending pod → scale-up → node appears (and bills) → pod
schedules.

★ Q3. Workload identity vs a service-account key file in a Secret —
why is WI categorically better?
**A:** A key file is a long-lived credential: exfiltratable from the
Secret/laptop/git, needs rotation, invisible blast radius. WI binds a
Kubernetes ServiceAccount to a GCP SA via federation: pods get
short-lived tokens from the metadata server, nothing to steal at rest,
revocation is an IAM edit, and permissions follow the workload not a
file.

★ Q4. Why do the nodes have no public IPs, and how do they still pull
images and reach Gemini?
**A:** Attack-surface reduction — nothing can connect inbound to a
node. Egress: private Google access for Google APIs/Artifact Registry
within the VPC path, Cloud NAT for the general internet (HF model
downloads, Gemini endpoint). NAT is outbound-only by construction.

Q5. Zonal cluster here — when would you insist on regional, and what
does it cost?
**A:** Regional replicates the control plane across 3 zones: API
server survives a zone outage (and node upgrades don't pause it).
Costs ~3× control-plane fee and loses the free-tier credit. For a lab:
zonal. For customer prod: regional, plus multi-zone node pools —
and note the data-plane (our single GPU) is still zonal, so honesty
about what regional actually buys.

Q6. Why are secondary IP ranges needed for pods (VPC-native)?
**A:** Pods get real VPC IPs from the `pods` secondary range via alias
IPs — routable without an overlay, visible to firewall rules/flow
logs, and required for some integrations (NEG load balancing).
Sizing matters: /16 for pods bounds cluster scale (IP exhaustion is a
classic GKE outage story).

Q7. What makes spot nodes acceptable for EVERY component of this
platform? Go component by component.
**A:** Gateway: 2 replicas, stateless — reschedules freely. Redis:
quota/cache loss is degraded-but-documented (lab). Mocks/agents:
stateless. vLLM: THE breaker turns its death into a routing decision —
stage 1 is precisely what buys permission for a spot GPU. Monitoring:
short gaps acceptable. Anything that can't tolerate 30s-notice death
shouldn't be on spot — here, nothing qualifies.

Q8. `terraform destroy` fails halfway. What's your recovery
procedure?
**A:** Read the error — usually dependency ordering or a resource
modified outside TF. `terraform state list` to see what's still owned;
re-run destroy (idempotent); for zombies, fix in console then
`terraform state rm` / `terraform refresh` to reconcile. Never hand-
delete something TF owns without telling the state.

## Interview drill topics
"Walk me through your VPC design." / "GPU quota request denied in
us-central1 — options?" (other zone/region, on-demand fallback var,
smaller model on CPU, stay on API fallback) / "How would you make this
multi-region?"
