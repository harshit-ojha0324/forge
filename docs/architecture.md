# Forge — Architecture

*Written as a customer-facing platform document: what you get, how it
behaves under failure, and what its limits are.*

## 1. What Forge is

Forge is a multi-tenant inference platform. Tenants (agent teams,
applications) get an OpenAI-compatible endpoint, an API key, and a daily
token budget. The platform decides which backend serves each request —
self-hosted vLLM on GPU when healthy, a hosted fallback (Gemini) when
not — and meters, traces, and dashboards everything.

## 2. Request path

```
client ── auth ── quota check ── cache lookup ── admission (queue) ── backend routing ── metering
                                     │ hit                                  │
                                     └─► response                    breaker CLOSED ─► vLLM ──► response
                                                                     breaker OPEN ───► Gemini ─► response
                                                                     vLLM fails ─────► Gemini (same request)
```

Ordering rationale:

1. **Auth before anything** — unauthenticated traffic never touches Redis
   or a backend.
2. **Quota before cache** — an exhausted tenant is out of tokens even for
   cached answers, keeping quota semantics predictable.
3. **Cache before admission** — a cache hit shouldn't consume a
   concurrency slot; during overload the cache still serves.
4. **Admission before backends** — concurrency to the GPU is the scarce
   resource; the queue protects vLLM's own scheduler from collapse.

## 3. The circuit breaker (the crown jewel)

States: `CLOSED → OPEN` after N consecutive failures; `OPEN → HALF_OPEN`
after a cooldown; one probe request decides `→ CLOSED` or back to `OPEN`.

Why not just retry each request against the primary first? Because during
an outage every request would pay the connect timeout before failing
over — with a 1s connect timeout and 8 concurrent slots, that's an extra
second of latency on 100% of traffic and slots held hostage. The breaker
converts "known dead" into a zero-cost routing decision, and the
half-open probe bounds recovery detection to one sacrificial request
instead of a thundering herd.

Failure taxonomy:

| Upstream behaviour | Breaker | Client sees |
|---|---|---|
| 2xx | success recorded, counter resets | response |
| 5xx / connect error / timeout | failure; failover in-request | response from fallback |
| 4xx (bad request, ctx too long) | success (upstream is alive) | the 4xx, unchanged |
| both backends fail | — | 502 `all_backends_failed` |

Streaming: failover is possible until the upstream accepts the request.
After the first byte reaches the client, a failure terminates the stream —
replaying on the fallback would emit duplicate/partial output. This
boundary is measured: `forge_ttft_seconds` tells you how large that
window is in practice.

## 4. Admission control

Two numbers: `max_concurrency` (slots running against backends) and
`queue_max_waiting` (bounded waiting room). Beyond both, requests are
shed immediately with 429 + `Retry-After`. A waiter that can't get a slot
within `queue_wait_timeout_s` gets 503.

Shedding early is deliberate: queueing beyond capacity increases latency
for everyone and throughput for no one. The queue exists to absorb
sub-second bursts, not to hide sustained overload — sustained overload
should be visible (429s, `forge_shed_total`) so capacity decisions get made.

## 5. Multi-tenancy

- **Identity**: API key → tenant, loaded from a Secret-mounted YAML.
- **Quotas**: daily token budgets in Redis (`forge:usage:{tenant}:{day}`),
  checked at admission, charged after the response when true usage is
  known. Concurrent requests can overshoot by up to `max_concurrency`
  requests — accepted; the alternative (pre-reserving unknown response
  lengths) rejects legitimate work.
- **Metering**: `forge_tokens_total{tenant,direction}` powers the
  per-tenant spend dashboard; `/v1/usage` lets tenants self-serve.
- **Isolation limits (honest)**: quotas bound spend per day, not fairness
  per second. A burst-heavy tenant can monopolize the queue window.
  Per-tenant rate limits/weighted fair queueing are the designed-for next
  step (the admission controller is the insertion point).

## 6. Caching

Exact-match Redis cache, only for `temperature=0, stream=false` requests
(hash of model + messages + sampling params). Sampled requests are never
cached — replaying one output for a distribution of valid outputs would
silently change behaviour. The interface is key/value so a semantic cache
(embed → ANN lookup) can replace `cache_key` without touching the flow.

## 7. Model naming

Clients send the public alias (`forge-default`). The gateway rewrites the
model per backend (`qwen2.5-3b-instruct` for vLLM, `gemini-2.0-flash` for
the fallback). Tenants can't tell which backend answered except via the
`x-forge-backend` debug header — model choice is a platform concern, so
swapping models requires no client change.

## 8. Observability

- **Metrics** (Prometheus): request rate by tenant/backend/outcome,
  latency + TTFT histograms, queue depth, in-flight, cache events,
  breaker state, shed count, failovers, tokens by tenant. GPU metrics via
  DCGM exporter when the GPU pool is up.
- **Traces** (OpenTelemetry → Jaeger): one span per request with tenant,
  backend, cache, and token attributes; agent traffic propagates context
  so a trace runs agent → gateway → backend.
- **Alerts** (SLOs): client-visible error rate > 1% (page), p95 > 5s
  (page), breaker open > 1m (warn), queue > 75% (warn). Alert text links
  the runbook.

## 9. Kubernetes & GitOps topology

- **Cluster**: zonal GKE, VPC-native, workload identity; custom VPC,
  Cloud NAT (nodes have no public IPs).
- **Node pools**: `services` (e2-standard-2, spot, 1–3) for everything
  stateless; `gpu-t4` (n1-standard-4 + T4, spot, **0–1**) exclusively for
  vLLM via taint + nodeSelector. Scheduling the vLLM pod is what scales
  the pool up; deleting the ArgoCD app returns it to zero.
- **GitOps**: ArgoCD app-of-apps. One manual `kubectl apply` of the root
  app; every service, chart bump, and config change after that ships by
  git push. `prune: true, selfHeal: true` means the cluster converges to
  git, including reverting drift.
- **CI gate**: unit tests → boot the full stack in CI → 26-check eval run
  including a failover drill → only then are images pushed for ArgoCD to
  roll out.

## 10. Failure modes walked through

| Failure | What happens |
|---|---|
| vLLM pod OOM/crash | connect errors → breaker opens after 3 → all traffic on Gemini → probe closes it after recovery. Zero client 5xx. |
| Spot GPU node preempted | same as above, plus pod reschedules; pool may re-provision a node (minutes). Runbook: `docs/runbook-gpu-node-loss.md`. |
| Redis down | **fail open**: quota checks pass (unmetered), cache silently bypassed, `forge_redis_errors_total` counts every absorbed failure. Serving is unaffected; `/v1/usage` degrades. Fail-closed would only be right if quotas were hard billing guarantees. |
| Gemini down while vLLM healthy | invisible (fallback unused). If vLLM *also* fails: 502s, `ForgeErrorRateSLOBreach` pages. |
| Traffic spike 3x | queue absorbs the burst, then 429s with Retry-After; `ForgeQueueSaturated` warns; dashboards show shed rate for the scaling decision. |
| Bad deploy of the gateway | eval gate blocks the image publish; if something ships anyway, ArgoCD rollback = git revert. |

## 11. Known limitations (v1)

- Redis is single-node (lab-grade) — Memorystore or Redis Sentinel for
  real use; its failure degrades metering/caching (fail-open) rather
  than serving.
- Breaker state is per-gateway-replica, not shared; replicas discover a
  dead primary independently (bounded by threshold × replicas extra failures).
- No per-second rate limiting or fair queueing between tenants (see §5).
- Exact-match cache only; semantic cache is a drop-in upgrade point.
- Single region, single GPU node — no cross-zone HA for the primary model.
