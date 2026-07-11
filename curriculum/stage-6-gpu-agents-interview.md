# Stage 6 — GPU serving, agents as tenants, interview mastery (2 days)

## Why this matters

This is where the two resumes fuse: the platform story (vLLM on spot
GPU with DCGM telemetry) and the agent story (LangGraph workloads as
metered tenants). It ends with the demo video and mock interviews —
the actual deliverables.

## Concepts

1. vLLM in one breath: continuous batching (new requests join the
   running batch between decode steps) + PagedAttention (KV-cache in
   pages → no fragmentation → bigger effective batch) = throughput.
2. Sizing arithmetic: Qwen2.5-3B fp16 ≈ 6GB weights; T4 = 16GB; the
   rest is KV cache; why `--dtype=half` (T4 is compute 7.5, no bf16)
   and what `--max-model-len` trades (context vs concurrent sequences).
3. The GPU scheduling chain: pod (toleration + nodeSelector + gpu
   resource limit) → autoscaler → node (driver via GKE) → device
   plugin exposes `nvidia.com/gpu` → container sees the GPU.
4. Agents as tenants: the agent's `base_url` is the gateway; platform
   concerns (model choice, failover, quotas, tracing) leave agent code
   entirely.
5. Model lifecycle on spot: every pod start re-downloads ~2GB and
   loads weights — startup probe budgets for it; what a PVC or
   GCS-fuse cache would change.

Reading: `deploy/helm/vllm/`, `services/agent-demo/agent.py`,
`docs/runbook-gpu-node-loss.md`, `docs/cost.md`.

## Labs

### Lab 6.1 — GPU day (do-together; budget ~$1)
Sync the vllm app in ArgoCD. Watch the chain live:
`kubectl get pods -w` (Pending) → `kubectl get nodes -w` (T4 node
joins, ~2–3 min) → vllm ContainerCreating → startup probe cycles while
the model downloads → Ready. Point the gateway's
`FORGE_PRIMARY_BASE_URL` at the real vLLM (git push, ArgoCD syncs),
run `make evals` against the cluster — real model, same gate.

### Lab 6.2 — The real failover demo (do-together ★)
Load against the cluster gateway; `kubectl -n forge delete pod -l
app.kubernetes.io/name=vllm` mid-run. Same zero-5xx result as local,
now with a real GPU workload. Record terminal + Grafana — this is the
money footage for the video. THEN: delete the vllm app, watch the node
drain away, confirm billing stops. Teardown is part of the lab.

### Lab 6.3 — Agent under the microscope (solo ★)
Run `agent.py --loop` as the smart-city tenant. In Grafana, find the
tenant's tokens/s; in Jaeger, find one agent request trace. Kill vLLM
mid-loop: the agent never errors (write down WHY — every stage-1
mechanism involved). Check `/v1/usage` before/after.

### Lab 6.4 — Runbook fire-drill (do-together)
Teacher plays the pager: "breaker open 10 minutes, vllm pod Pending."
You drive kubectl + the runbook to diagnosis (spot capacity vs quota
vs crash-loop) out loud. Time-boxed: 15 minutes to a verdict.

### Lab 6.5 — Ship the deliverables (solo)
Record the 2-minute video (docs/demo-script.md). Update the README
with the video link and YOUR numbers (req/s, p95, token counts from
your runs). Write the two resume-bullet sets — with real measured
numbers, no placeholders.

## Break-it drill

Set `--max-model-len=32768` on the vllm chart (too big for a T4 with
this model's KV needs at high utilization) and watch how it fails
(vLLM refuses to start / KV-cache errors) — then explain the sizing
arithmetic that predicts it. Restore.

## Teach-back exam

★ Q1. Continuous batching vs static batching — why does it matter for
an API workload?
**A:** Static batching waits to assemble a batch, then runs it to
completion — arrivals mid-batch wait, finished sequences hold slots.
Continuous batching admits/evicts sequences at every decode step: new
requests join immediately, done ones free memory instantly. For bursty
API traffic that's the difference between GPU idle-time + queuing
spikes and steady high utilization at low TTFT.

★ Q2. What does PagedAttention actually fix?
**A:** KV cache was allocated as one contiguous region per sequence
sized for max length — massive internal fragmentation and up-front
over-reservation. Paging the KV cache (fixed-size blocks, virtual-
memory style) allocates as sequences actually grow, so the same VRAM
holds far more concurrent sequences → bigger effective batch →
throughput.

★ Q3. Walk the full chain from "ArgoCD syncs the vllm app" to "the
gateway's probe closes the breaker." Every actor.
**A:** Sync creates Deployment → pod Pending (needs nvidia.com/gpu,
tolerates the taint, selects pool=gpu) → cluster autoscaler sees an
unschedulable pod whose requirements match the gpu-t4 pool → spot VM
created, joins, GKE installs the driver, device plugin advertises the
GPU → pod schedules, vLLM downloads weights, loads, /health passes
startup probe → Service endpoints update → gateway's next half-open
probe hits vLLM 200 → breaker CLOSED → traffic shifts home.

★ Q4. Why does the agent contain zero failover/retry/model-selection
code, and why is that the architecturally right place?
**A:** Its only integration is `base_url` = the gateway, which owns
backend choice, failover, quotas, caching, and telemetry. Platform
concerns change on platform cadence (new model, new fallback) — with N
agent teams you want that in one place, not N codebases. The agent
proves it: vLLM died mid-loop and the agent's code path never noticed.

Q5. T4 + Qwen2.5-3B sizing: justify dtype, and what happens to
concurrency if you double max-model-len?
**A:** fp16 because T4 (compute 7.5) has no bfloat16; weights ~6GB of
16GB, remainder mostly KV cache. KV usage scales with tokens ×
layers × heads — doubling max-model-len roughly doubles worst-case KV
per sequence, so the same cache holds ~half the concurrent sequences;
throughput drops or requests queue/preempt. Context length is a
capacity currency.

Q6. Every vLLM pod start downloads ~2GB. When does that become
unacceptable and what do you change?
**A:** Fine at 5-min tolerable recovery with an API fallback carrying
traffic. Unacceptable when recovery time is SLO-relevant or restarts
are frequent (spot churn). Fixes in order: regional PVC or GCS-fuse
model cache (download once), a hosted-model image bake, or a warm
standby replica in a second zone — each trades cost for RTO.

Q7. Your quota counts tokens, and vLLM/Gemini bill differently. How do
you turn tokens into per-tenant COST?
**A:** Tokens × per-backend unit price. The metrics already split by
backend (`forge_requests_total`) and tenant (`forge_tokens_total`);
adding a backend label to the token counter (or a recording rule
joining the two) gives spend-by-tenant-by-backend — self-hosted
amortized $/token vs API list price. That's the FinOps answer
customers actually want.

## Final boss: the mock interview (90 min)

The teacher runs two loops using everything above:
1. **Platform loop** — kill scenarios, scale-out ("10× tenants
   tomorrow"), cost defense, "why not just use OpenRouter/Bedrock?"
2. **Agentic loop** — eval-gated CD deep-dive, agent observability,
   "how would you A/B two models under live agents?"

Pass = no question where you have nothing structured to say. Log the
weak spots in PROGRESS.md; they're your pre-interview review sheet.
