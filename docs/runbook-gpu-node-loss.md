# Runbook — GPU node lost (spot preemption or failure)

**Severity when it fires:** none for clients (failover holds); cost/latency
profile changes. Treat as a warn, not a page, unless the fallback also fails.

## What you'll see

- Alert: `ForgeBreakerOpen` (warn) after ~1 minute.
- Grafana (`Forge — Inference Platform Overview`): "Requests/s by Serving
  Backend" shifts from `vllm` to `gemini`; breaker stat goes red `OPEN`;
  client error % stays ~0.
- `kubectl get nodes` — the `gpu-t4` pool node is `NotReady` or gone.
- `kubectl -n forge get pods` — `vllm-...` is `Pending` (waiting for a node).

## Why this is expected behaviour

The GPU pool is **spot capacity**: GCP can reclaim the node with 30s
notice at any time. The platform is designed so that this is a non-event
for clients: the gateway's circuit breaker opens after 3 consecutive
failures and routes everything to the Gemini fallback. Nothing needs to
happen fast.

## Automatic recovery sequence (no action needed)

1. Cluster autoscaler sees the Pending vLLM pod and requests a new spot
   T4 node (usually 1–3 min; can be longer if the zone has no spot T4s).
2. Node joins, driver installs (GKE-managed), image pulls, vLLM downloads
   the model into its cache (~2 GB) and loads it (2–5 min total).
3. vLLM readiness passes; the gateway's next half-open probe succeeds;
   breaker closes; traffic returns to `vllm` on the dashboard.

Total: typically **5–10 minutes**, all hands-off.

## Manual checks if it hasn't recovered in 15 minutes

```bash
# is a node coming?
kubectl get nodes -l pool=gpu
kubectl -n forge describe pod -l app.kubernetes.io/name=vllm | tail -20

# common cause 1: no spot T4 capacity in the zone right now
# (events show "no available instances" / scale-up failed)
#   option a: wait — spot capacity fluctuates
#   option b: temporarily switch the pool to on-demand:
#     terraform apply -var 'gpu_spot=false'   # if you added the variable
#   option c: stay on fallback — clients are unaffected; decide by cost

# common cause 2: GPU quota exhausted (first deploy only)
# events show "Quota 'NVIDIA_T4_GPUS' exceeded" -> docs/gcp-setup.md §quota

# common cause 3: vLLM crash-looping after node arrival
kubectl -n forge logs deploy/vllm --tail 50
# OOM -> lower gpuMemoryUtilization or maxModelLen in deploy/helm/vllm/values.yaml
```

## Verifying client impact (should be none)

```bash
# error rate over the incident window — expect ~0
curl -s 'http://<prometheus>/api/v1/query?query=sum(rate(forge_requests_total{outcome=~"all_backends_failed|stream_error"}[15m]))'

# what served traffic during the outage
curl -s 'http://<prometheus>/api/v1/query?query=sum by (backend) (rate(forge_requests_total{outcome="ok"}[15m]))'
```

## Cost note

While the breaker is open you're paying the fallback's per-token price
instead of the (cheaper-at-utilization) self-hosted GPU. The per-tenant
token dashboard tells you what the incident cost. There is no urgency to
force recovery — the trade is availability for a slightly different cost
profile, which is the design.

## If the fallback ALSO fails

Now it's a page (`ForgeErrorRateSLOBreach`). Check Gemini status/keys and
network egress (Cloud NAT). Both backends down means clients get 502s —
communicate, then restore whichever backend is faster.
