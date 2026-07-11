# Forge — a multi-tenant LLM inference platform

> I didn't just build agents — I built the platform they run on.

Forge is an inference gateway + agent runtime for Kubernetes: an
OpenAI-compatible API in front of self-hosted **vLLM** (spot T4 GPU on GKE)
with **circuit-breaker failover to Gemini**, per-tenant API keys and token
quotas, admission control with load shedding, a Redis response cache, and
full observability (Prometheus, Grafana, OpenTelemetry). Everything is
provisioned by **Terraform** and deployed by **ArgoCD** — a git push is the
only deploy mechanism. LangGraph agents run on the platform as tenant #1,
and CI **gates image publishing on a 20-prompt eval run** that includes a
live failover drill.

## The demo

Sustained load hits the gateway. Mid-run, the primary model server is
killed. Traffic fails over to the fallback with **zero client-visible 5xx**;
the circuit breaker opens, then probes and recovers when the primary
returns.

```
make demo        # runs it: load + kill + revive, Grafana at localhost:3000
```

```
   t  ok/vllm  ok/gemini  429  5xx
   18      21          0    0    0
   19      20          0    0    0   <- primary killed here
   20       0         19    0    0   <- breaker open, fallback serving
   ...
   41       0         22    0    0   <- primary revived
   42      18          3    0    0   <- half-open probe passed, recovered
```

## Architecture

```
                        ┌────────────────────────── GKE (Terraform) ───────────────────────────┐
                        │                                                                      │
  LangGraph agents ──►  │  Forge Gateway ──────► vLLM (Qwen2.5-3B, spot T4, scale-to-zero)     │
  (tenant #1)           │   • auth + quotas         │  ▲ DCGM GPU metrics                      │
  loadtest (tenant #2)  │   • admission queue       │  breaker OPEN? probe?                    │
                        │   • response cache        ▼                                          │
                        │   • circuit breaker ──► Gemini (OpenAI-compat endpoint, fallback)    │
                        │   • Prometheus /metrics + OTel traces                                │
                        │                                                                      │
                        │  ArgoCD (app-of-apps) ◄── git push        kube-prometheus-stack      │
                        └──────────────────────────────────────────────────────────────────────┘
```

The same containers run locally via docker-compose with mock model
servers — the failover demo, dashboards, traces, and eval gate all work
on a laptop with no GPU and no cloud bill.

## Quickstart (local, no cloud needed)

```bash
make up          # gateway :8080, Grafana :3000, Prometheus :9090, Jaeger :16686
make evals       # the 26-check eval gate, incl. a failover drill
make demo        # the failover demo under load
```

Call it like any OpenAI endpoint:

```bash
curl -s localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer forge-demo-localdev-key" -H "Content-Type: application/json" \
  -d '{"model":"forge-default","messages":[{"role":"user","content":"hello"}]}'
```

## Repo map

| Path | What it is |
|---|---|
| `services/gateway/` | The inference gateway (FastAPI) — quotas, queueing, cache, breaker, failover. 23 unit tests. |
| `services/mock-llm/` | OpenAI-compatible mock model server (local dev + failover drills) |
| `services/agent-demo/` | LangGraph smart-city agent running as tenant #1 |
| `infra/terraform/` | GKE, VPC, NAT, IAM/workload identity, CPU + spot-T4 node pools, Artifact Registry |
| `deploy/helm/` | Charts: gateway, vllm, redis, mock-llm |
| `deploy/argocd/` | App-of-apps: one `kubectl apply`, then git is the deploy button |
| `deploy/local/` | docker-compose stack mirroring the cluster |
| `observability/` | Grafana dashboard, Prometheus SLO alert rules |
| `evals/` | The 20-prompt eval set + gate script CI runs before publishing |
| `loadtest/` | Python load generator + k6 profile |
| `docs/` | Architecture, runbook, cost breakdown, GCP setup, demo script |
| `curriculum/` | Stage-by-stage rebuild-and-learn curriculum with teach-back question banks |

## Deployment stages

Each stage is independently useful; cut from the bottom.

1. **Local platform** — everything above on docker-compose. *(done)*
2. **GKE foundation** — Terraform apply, ArgoCD, gateway + mocks on the cluster, CI publishing to Artifact Registry. *(docs/gcp-setup.md)*
3. **Observability on cluster** — kube-prometheus-stack, dashboards, SLO alerts.
4. **GPU serving** — vLLM on the spot T4 pool, DCGM GPU metrics, real failover demo.
5. **Agents + eval-gated CD** — agent workloads as tenants, evals gating rollout.

## Key design decisions

- **Failover, not retry-only**: the breaker skips a known-dead primary
  entirely, so requests during an outage don't pay a timeout before
  reaching the fallback. Half-open probes bring traffic back without a
  thundering herd.
- **Failover boundary for streams**: streams fail over until the upstream
  accepts the request; after first byte, a failure terminates the stream
  (replaying a half-sent stream would duplicate output).
- **Shed early, queue small**: a bounded queue absorbs bursts; beyond it
  the gateway 429s immediately. Queueing past capacity adds latency, not
  throughput.
- **4xx never fails over**: a bad request is the caller's fault; only 5xx
  and transport errors count against the breaker.
- **Cache only `temperature=0`**: replaying sampled completions would
  silently change model behaviour.
- **Quota check at admission, charge after response**: pre-reserving
  tokens for an unknown-length response would reject work needlessly;
  bounded overshoot is documented and accepted.
- **Spot GPU, scale-to-zero**: the vLLM pod being scheduled is what
  summons the GPU node; deleting the app returns the bill to ~$0.
