# Stage 5 — Helm, ArgoCD & the eval gate (1.5 days)

## Why this matters

"Deployed via GitOps" is common; "CI gates image publishing on an eval
run that includes a failover drill" is not. This stage makes you able
to defend both — and eval-gated CD is your differentiator as an
agentic-AI candidate.

## Concepts

1. Helm: charts as templates + values; release = chart × values ×
   cluster; where templating ends and operators begin.
2. GitOps inversion: CI pushes artifacts, the cluster PULLS desired
   state from git (ArgoCD reconciler). Drift detection + self-heal.
3. App-of-apps: one root Application whose "resources" are other
   Applications — the cluster's table of contents in git.
4. Sync policies: automated vs manual (why vllm is manual = a cost
   switch), prune, selfHeal.
5. Deploy gating: tests prove code, evals prove *behaviour*; gate at
   the artifact-publish step so ArgoCD can only ever see good images.

Reading: `deploy/helm/forge-gateway/` end to end,
`deploy/argocd/root-app.yaml` + `apps/`, `.github/workflows/ci.yml`,
`evals/run_evals.py`.

## Labs

### Lab 5.1 — Template forensics (do-together)
`helm template deploy/helm/forge-gateway | less`: for each rendered
resource, point to the template + values lines that produced it.
Explain the `checksum/tenants` annotation trick (config change →
checksum change → pod template change → rollout).

### Lab 5.2 — Rebuild the gateway chart (rebuild-solo ★)
Delete `deploy/helm/forge-gateway/templates/`, rewrite from the
rendered output you studied (Deployment, Service, SA with WI
annotation, tenants Secret, ServiceMonitor behind a flag). Grade:
`helm lint` + `helm template` diff vs git — differences must be
explainable, then `git checkout` the reference or keep yours if tests
pass on cluster.

### Lab 5.3 — GitOps in anger (do-together, needs stage 4 cluster)
Change `replicas: 2→3` in values, push, watch ArgoCD roll it. Then
`kubectl scale deploy forge-gateway --replicas=1` by hand and watch
selfHeal put it back — say out loud why that's the whole point.
Then a rollback: `git revert`, push, watch.

### Lab 5.4 — Extend the eval gate (rebuild-solo ★)
Add a check to `evals/run_evals.py`: p95 across the 20 prompts must be
under half the SLO (regression canary, not just hard failures). Break
it on purpose (raise the mock's TTFT env var), watch the gate FAIL,
restore, watch it pass. That loop is the eval-gated-CD story told in
30 seconds.

## Break-it drill

Commit a deliberately broken values change (bad image tag), push,
watch ArgoCD: sync succeeds but the rollout sticks (ImagePullBackOff),
app shows Degraded. Old pods keep serving (rolling update never killed
them). Recover via git revert — never kubectl. Explain why the
blast radius was zero.

## Teach-back exam

★ Q1. CI-push deploys vs GitOps pull — what does the inversion buy?
**A:** (1) Cluster credentials never leave the cluster — CI holds no
kubeconfig. (2) Git is the complete, auditable desired state — the
diff IS the change review, rollback is revert. (3) Continuous
reconciliation: drift (manual kubectl, deleted objects) is detected
and healed, not just deployed-over next release. (4) Recreating the
cluster = point ArgoCD at the repo.

★ Q2. Why gate at image-publish rather than having ArgoCD run evals?
**A:** Make bad artifacts unrepresentable: if the image never reaches
the registry, no sync — automated or manual, now or later — can ship
it. Gating inside CD would need sync hooks and leaves the bad artifact
lying around for someone to promote. Publish-gating also runs evals
once per artifact, not once per environment.

★ Q3. What do this project's evals prove that unit tests don't, and
what would they prove with a real model?
**A:** Unit tests prove component logic in isolation with fakes. The
eval gate boots the REAL composed system and proves behaviour:
end-to-end auth, streaming framing, cache semantics, latency SLO, and
a live failover drill — integration properties no unit test touches.
With real models, the same harness carries quality checks (grounded-
response rate) so a model/prompt regression blocks deploys exactly like
a code bug — that's eval-gated CD for agentic systems.

★ Q4. Why is the vllm Application manual-sync while everything else is
automated?
**A:** Syncing vllm schedules a GPU-tolerating pod → autoscaler summons
a spot T4 → billing starts. Manual sync makes GPU cost an explicit
human action (and delete = scale-to-zero), while stateless free-tier
services converge automatically. Sync policy as a cost-control lever.

Q5. What is `prune: true` protecting against, and what's its risk?
**A:** Orphans: delete a manifest from git and prune removes the live
object, keeping cluster ≡ git. Risk: a bad git change (or wrong
targetRevision) mass-deletes real resources — mitigations: PR review,
protected branches; ArgoCD also has selective sync/prune-propagation
controls.

Q6. A secret (Gemini API key) must reach the gateway. Walk the
GitOps-safe path.
**A:** Never in git. v1: create the Secret out-of-band, reference via
`envFromSecret`/`tenantsExistingSecret`. Production: External Secrets
Operator or Sealed Secrets — git holds an encrypted/reference object,
the controller materializes the real Secret in-cluster. State the
trade: ESO adds a dependency; sealed-secrets couples to a cluster key.

Q7. Helm rollout: how does changing ONLY the tenants Secret restart
pods, and why is that needed?
**A:** The Deployment's pod template has
`checksum/tenants: sha256(values)` annotation; value change → template
hash change → new ReplicaSet → rolling restart. Without it, the Secret
updates but running pods keep the old file until something else
restarts them — classic silent-config-drift bug.

## Interview drill topics
"Your ArgoCD shows Synced but the app is down — debug." / "Design
canary releases on top of this." (Argo Rollouts / two Applications
with weighted Service) / "Where do database migrations fit in
GitOps?"
