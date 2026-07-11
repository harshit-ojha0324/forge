# Stage 2 — Containers & the local platform (1 day)

## Why this matters

The local stack is the platform in miniature. Interviewers respect
"the whole thing runs on my laptop with failure injection" — it shows
you know what a dev-prod parity story is. This stage also builds the
Docker fluency stage 4–5 assume.

## Concepts

1. Image vs container; layers and caching; why requirements.txt is
   COPYied before app code in the Dockerfile.
2. Compose networking: service DNS names, ports vs expose,
   depends_on's (weak) guarantees.
3. The mock strategy: mocks implement the same OpenAI protocol as the
   real backends, so the gateway cannot tell — which is why local tests
   prove real behaviour. (Contract testing in disguise.)
4. Failure injection as a first-class feature (`/control fail=true`).
5. Env-var config: one image, many environments (compose vs Helm set
   the same FORGE_* knobs).

Reading: `services/gateway/Dockerfile`, `services/mock-llm/app.py`,
`deploy/local/docker-compose.yml`.

## Labs

### Lab 2.1 — Explain the compose file line by line (do-together)
For every service: why does it exist, what breaks without it, what
talks to what. Draw the network graph from memory, then check.

### Lab 2.2 — Rebuild the mock (rebuild-solo ★)
Delete `services/mock-llm/app.py`. Rewrite from the spec: OpenAI
chat-completions (unary + SSE streaming with a final usage chunk),
`/healthz`, `/control` failure toggle, env-tunable TTFT and
tokens/sec. Grade: `make up && make evals` — all 25 checks pass.

### Lab 2.3 — Add a third mock tenant scenario (do-together)
Add a `mock-claude` service to compose (same image, different env),
point `FORGE_FALLBACK_BASE_URL` at it, re-run the failover drill.
Understand exactly which env vars had to move.

### Lab 2.4 — Layer-cache experiment (solo, 15 min)
`time docker compose build gateway` twice: once after touching
`app/routes.py`, once after touching `requirements.txt`. Explain the
timing difference from layer caching.

## Break-it drill

`docker compose stop redis` under light load. Observe: which endpoints
still work (`/healthz`, `/v1/models`) and which fail (chat — quota
check throws). Connect what you see to stage 1 Q10, then restart redis
and confirm recovery without gateway restart (connection pool
reconnects).

## Teach-back exam

★ Q1. Why do the mocks make local testing *valid* rather than just
convenient?
**A:** The gateway speaks a protocol, not a vendor SDK. The mocks
implement the same protocol surface (status codes, SSE framing, usage
fields, failure modes), so every gateway code path exercised locally —
auth, failover, streaming, metering — is the same code path production
runs. What is NOT covered: real model latency distributions, GPU
behaviour, tool-calling — and you should say so.

★ Q2. Gateway reaches mock-vllm at `http://mock-vllm:8000` — who
resolves that name, and what's the GKE equivalent?
**A:** Compose puts services on a shared network with an embedded DNS
that maps service name → container IP. On Kubernetes the equivalent is
a Service (cluster DNS name → ClusterIP → pods via kube-proxy). The
gateway config just swaps the hostname — that symmetry is deliberate.

Q3. Why does `depends_on` not guarantee the gateway starts after Redis
is *ready*, and why is that OK here?
**A:** depends_on orders container *start*, not readiness. The gateway
tolerates it: redis client connects lazily and requests fail fast until
ready; probes/retries cover the gap. (With conditions like
service_healthy you can do better; the robust fix is always
retry-on-dependency, not start ordering.)

Q4. Why COPY requirements.txt and pip install *before* COPYing app/?
**A:** Layer caching: dependencies change rarely, code changes
constantly. This ordering means a code edit rebuilds only the cheap
final layers instead of re-running pip install.

Q5. One image runs locally and on GKE. Where does each environment
inject its config, and why is that better than baking config in?
**A:** Compose `environment:` vs Helm values → env. Same artifact
promoted through environments (what you tested is what you ship);
config drift is visible in git, not hidden in image variants.

## Interview drill topics
"How would you test failover without killing real infra?" / "What's
your dev-prod parity story?" / "Why not testcontainers/kind for this?"
