# Stage 3 — Observability: Prometheus, Grafana, OTel, SLOs (1.5 days)

## Why this matters

Every senior loop asks "how do you know it's working?" — and the
Rafay-style customer-facing roles live in dashboards and SLOs. This
stage turns the dashboard you already have into things you can derive
from scratch.

## Concepts

1. Metric types: Counter, Gauge, Histogram — and why you `rate()` a
   counter instead of reading it.
2. Histograms & quantiles: buckets, `histogram_quantile`, why you can't
   average percentiles across replicas.
3. Cardinality: why metrics are labeled by tenant (bounded) but never
   by request-id (unbounded).
4. SLO thinking: client-visible error rate vs internal failures —
   failover means backend death is NOT an SLO event.
5. Alert design: `for:` duration, page vs warn, alert → runbook link.
6. Metrics vs traces: aggregates vs individual request narratives;
   what OTel spans/attributes/context propagation are.

Reading: `app/metrics.py`, `app/tracing.py`,
`observability/prometheus/alerts.yml`, the dashboard JSON (skim), and
`docs/architecture.md` §8.

## Labs

### Lab 3.1 — PromQL kata (solo ★)
With the stack under `make loadgen`, write from scratch in the
Prometheus UI (no peeking at the dashboard): requests/s by backend;
p95 latency; client-visible error %; cache hit ratio; tokens/day by
tenant; time-in-state for the breaker. Keep a cheat-sheet you wrote
yourself.

### Lab 3.2 — Read the demo in the graphs (do-together)
Run `make demo`. Using only Grafana (no terminal output), narrate the
timeline: when the kill happened, detection lag, what served traffic,
when the probe closed the breaker. This narration IS the demo-video
voiceover.

### Lab 3.3 — Add a metric end-to-end (rebuild-solo ★)
Add `forge_queue_wait_seconds` (histogram): time each admitted request
spent waiting for a slot. Instrument admission.py, add a dashboard
panel (p95), and an alert (warn if p95 wait > 1s for 2m). Prove it
moves under `make loadgen` with lowered `FORGE_MAX_CONCURRENCY`.

### Lab 3.4 — Trace archaeology (do-together)
In Jaeger, find one failed-over request's trace. Which attributes tell
you the backend switched? Add one attribute you wish existed (e.g.
`forge.failover=true`), redeploy, find it again.

## Break-it drill

Stop Prometheus for 5 minutes under load, restart. What's on the
dashboard for the gap (nothing — pull model, no backfill)? What
happened to the counters (nothing — they live in the gateway process;
`rate()` bridges the gap)? Why is pull + in-process counters more
robust than push here?

## Teach-back exam

★ Q1. Why `rate(forge_requests_total[1m])` instead of the raw counter?
**A:** Counters are monotonic cumulative totals — the raw value is
"since process start", which conflates age with traffic. `rate()`
takes the per-second derivative over the window, and it's
reset-aware: a process restart (counter back to 0) doesn't produce a
negative spike.

★ Q2. Explain how p95 comes out of a histogram, and one artifact of
bucket boundaries.
**A:** The histogram exports cumulative bucket counters (`le=`).
`histogram_quantile(0.95, ...)` finds the bucket where the 95th
percentile falls and linearly interpolates within it. Artifact:
resolution is bucket-width — if p95 lands in the 1–2s bucket you get a
value interpolated inside that range, so bucket edges should track your
SLO thresholds (ours: TTFT buckets are sub-second-dense, latency
buckets log-spaced).

★ Q3. Why is labeling by tenant fine but labeling by request-id fatal?
**A:** Every label combination is a separate time series held in
memory. Tenants: dozens — bounded. Request-ids: unbounded → series
explosion, scrape bloat, OOM. Rule: labels are dimensions you aggregate
over, never identities. Identities go in traces/logs.

★ Q4. During the failover demo, vLLM was dead for 15s. Why did no
alert page?
**A:** By design, twice over. The paging SLO counts *client-visible*
failures — failover kept those at zero, so `ForgeErrorRateSLOBreach`
never moved. `ForgeBreakerOpen` (a warn) has `for: 1m` — a 15s outage
self-healed before it qualified. Alerts should fire on symptoms
(clients hurt) not causes (a backend died), and short self-healing
blips shouldn't wake humans.

Q5. Metrics vs traces — when do you reach for which?
**A:** Metrics answer "how much/how often/is it trending" — cheap,
aggregated, alertable. Traces answer "what happened to THIS request"
— the causal chain across services with timing. Debug loop: alert on a
metric, scope with dashboard, explain with traces.

Q6. Prometheus pulls /metrics. Give one real advantage of pull here,
and how the gap-during-outage behaves.
**A:** Pull means the monitored service holds no delivery state and
can't be backpressured by a slow collector; target health is free
(`up`). If Prometheus is down, there's simply no data for the gap —
but counters keep accumulating in-process, so post-restart `rate()`
over the gap edge still yields sane values. No backfill, honest gap.

Q7. Where would GPU metrics come from in stage 6, and name two you'd
actually watch.
**A:** DCGM exporter DaemonSet on the GPU node → ServiceMonitor →
Prometheus. Watch GPU utilization (are we paying for idle silicon) and
GPU memory (KV-cache headroom → correlates with vLLM preemptions/OOM);
temperature/power for spot-node health.

## Interview drill topics
"Define an SLO for this platform and defend the number." / "Your p95
looks fine but customers complain — what's wrong?" (answer touches:
per-tenant breakdown, p99/max, TTFT vs total, histogram averaging
sins) / "How do you monitor the monitoring?"
