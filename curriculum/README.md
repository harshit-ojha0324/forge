# Forge Curriculum — learn the platform you already own

This directory turns the finished Forge platform into a 2–3 week
learning program. v1.0 was built AI-assisted and verified working; the
goal here is that **you** can defend every design decision in an
interview and rebuild any component from a blank file.

## How to run it

1. Open a new Claude session in this repo.
2. Paste `TEACHING_PROMPT.md` as your first message (or tell Claude:
   "read curriculum/TEACHING_PROMPT.md and be my teacher").
3. Work through the stages in order, ~3–4 hours/day. Each stage ends
   with a **teach-back exam** the teacher must hold you to.

| Stage | File | What you'll own afterwards | Est. |
|---|---|---|---|
| 1 | `stage-1-gateway-core.md` | Circuit breaker, admission control, quotas, caching — rebuilt from scratch | 2 days |
| 2 | `stage-2-local-platform.md` | Docker, compose, the mock strategy, failure injection | 1 day |
| 3 | `stage-3-observability.md` | Prometheus/PromQL, histograms, SLOs, Grafana, OTel | 1.5 days |
| 4 | `stage-4-terraform-gke.md` | Terraform, VPC, GKE, workload identity, spot GPU economics — and the real cloud deploy | 2 days |
| 5 | `stage-5-gitops-helm-argocd.md` | Helm templating, ArgoCD app-of-apps, eval-gated CI | 1.5 days |
| 6 | `stage-6-gpu-agents-interview.md` | vLLM on T4, DCGM, agents as tenants, demo video, interview drills | 2 days |

## The rules (what makes this hybrid mode honest)

- **Rebuild, don't reread.** For crown-jewel components the lab deletes
  the file and you rewrite it. The reference implementation is one
  `git checkout` away if you sink.
- **Teach-backs gate progress.** You answer out loud/in writing without
  looking at code. The teacher checks against the model answers and does
  NOT advance you below the pass bar.
- **Break something every stage.** You don't understand a system until
  you've watched it fail. Every stage has a break-it drill.
- **The teacher writes boilerplate, you write decisions.** Dashboard
  JSON and Dockerfiles can be generated; breaker logic and Terraform
  resources you type yourself.

## Repo state assumed

Everything in this repo already works: `make test` (23 unit tests),
`make up` + `make evals` (25 checks incl. failover drill), `make demo`
(zero-5xx failover under load). If any of that fails, fix the
environment first — docs and Makefile are the map.
