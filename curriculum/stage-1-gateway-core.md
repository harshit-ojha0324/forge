# Stage 1 — The gateway core (2 days)

The crown jewel. Everything in this stage is guaranteed interview
material for any platform/distributed-systems loop.

## Why this matters

"Multi-tenant inference gateway with circuit-breaker failover" is the
headline resume line. The follow-up questions are always the same:
breaker mechanics, backpressure, what the client sees during failure,
and quota semantics. This stage makes those answers reflexive.

## Concepts (teacher briefs these before code is shown)

1. Failure detection vs failure handling: timeouts, consecutive-failure
   counting, why "retry the primary every time" is worse than a breaker.
2. The three breaker states and why HALF_OPEN exists at all.
3. Backpressure: bounded concurrency, bounded queues, load shedding.
   Little's Law intuition: queueing past capacity adds latency, never
   throughput.
4. Quota semantics: check-then-charge vs reserve-then-reconcile.
5. Cache correctness for LLMs: why sampling temperature decides
   cacheability.
6. The failover boundary for streaming responses.

Reading order: `app/breaker.py` → `app/admission.py` → `app/quotas.py`
→ `app/cache.py` → `app/backends.py` → `app/routes.py`.

## Labs

### Lab 1.1 — Rebuild the circuit breaker (rebuild-solo ★)
Delete `services/gateway/app/breaker.py`. Rewrite it from this spec:
CLOSED→OPEN after N consecutive failures; OPEN→HALF_OPEN after a
cooldown; exactly ONE probe allowed in HALF_OPEN; probe success →
CLOSED, probe failure → OPEN with fresh cooldown; success anywhere
resets the failure count. Grade: `make test` — the breaker unit tests
must pass unmodified.

### Lab 1.2 — Rebuild admission control (rebuild-solo ★)
Delete `app/admission.py`, rewrite from the spec in its docstring
(which you may keep). The trap you must rediscover: the fast path must
not yield to the event loop, or burst traffic gets shed while slots are
free. If your version uses `asyncio.Semaphore` + `wait_for`, run
`test_queue_sheds_load_beyond_capacity` and watch it fail — then fix it.

### Lab 1.3 — Trace a request (do-together)
On paper, no code: draw the full journey of (a) a happy request,
(b) a request during a vLLM outage, (c) a request when the tenant is
out of quota, (d) request #33 when 8 are running and 32 waiting.
Then verify each against `routes.py`.

### Lab 1.4 — Write a failing test first (do-together)
Add a test: when the primary times out (not 5xx — actually hangs), the
request fails over within `connect_timeout + ε` and the client still
gets 200. Make it pass if it doesn't.

## Break-it drill

Set `FORGE_BREAKER_COOLDOWN_S=2` and `FORGE_BREAKER_FAILURE_THRESHOLD=1`
in the compose file, `make up`, toggle the mock's `/control fail=true`,
and watch `curl -s localhost:8080/metrics | grep breaker` cycle
0→2→1→2… while the fallback serves. Explain each transition out loud as
it happens.

## Teach-back exam

★ Q1. Why is a circuit breaker better than retrying the primary on
every request during an outage?
**A:** Retrying makes every request pay the timeout/connect cost before
failing over — added latency on 100% of traffic and held concurrency
slots. The breaker converts "known dead" into a routing decision:
requests skip the corpse for free. Cost of the trade: detection lag
(threshold × failures) and one sacrificial probe per cooldown.

★ Q2. Why does HALF_OPEN allow exactly one probe instead of letting all
traffic retry after the cooldown?
**A:** If the primary is still dead, all-at-once retry re-fails a full
wave of requests (they still succeed via failover, but latency spikes
and the breaker's failure counting becomes noisy); if it just recovered,
a thundering herd can knock over a cold backend (empty caches, model
still loading). One probe bounds the blast radius to one request.

★ Q3. A request arrives, 8 in flight, 32 waiting. What happens and why
is that the right answer?
**A:** Immediate 429 with Retry-After — load shedding. Admitting it
adds latency for everyone and throughput for no one (capacity is fixed);
shedding early gives the client its budget back to retry elsewhere.
Visible shed rate is also the capacity-planning signal.

★ Q4. Why check quota before the cache lookup but charge tokens after
the response?
**A:** Check-before-cache keeps semantics simple: out of tokens means no
service, cached or not. Charge-after because true usage isn't known
until the response exists; pre-reserving an estimate either rejects
legitimate work or needs reconciliation. Trade-off: concurrent requests
can overshoot the daily quota by ≤ max_concurrency requests — bounded
and accepted.

Q5. Why does a 4xx from vLLM NOT trip the breaker or fail over?
**A:** 4xx means the upstream is alive and judged the request itself
bad (malformed, context too long). Failing over would send a bad request
to a second backend (waste, maybe different error), and counting it as
backend failure would open the breaker on healthy infrastructure —
client bugs would look like outages.

Q6. Why is only temperature=0 cached?
**A:** With sampling, one prompt maps to a distribution of valid
outputs; caching pins one sample and silently changes model behaviour
(and kills output diversity). temperature=0 is (near-)deterministic, so
replay is semantically safe.

Q7. When can a streaming request fail over, and why is there a
boundary?
**A:** Until the upstream accepts the request (before the first byte is
forwarded). After that the client has partial output; replaying on
another backend would duplicate or diverge (different model, different
tokens). Post-first-byte failures terminate the stream and count against
the breaker.

Q8. Your gateway runs 3 replicas. What's wrong with the breaker now?
**A:** State is per-replica: each must independently discover the
outage (3× threshold total failures — all still failovers, not client
errors) and each probes separately. Fix if needed: share state via
Redis or gossip; costs coordination latency and a new failure mode, so
per-replica is a reasonable v1.

Q9. Where does backpressure actually protect vLLM, and what happens
without it?
**A:** The scarce resource is GPU batch capacity. Unbounded concurrent
requests grow vLLM's own queue: KV-cache pressure, preemptions,
latency collapse for all in-flight requests. The gateway's semaphore
keeps the offered load at a level the GPU can batch efficiently and
turns the excess into fast, honest 429s.

Q10. Quota is stored in Redis. Redis dies — what does Forge do, and
when would you choose the opposite?
**A:** Fail open: quota checks pass (unmetered), cache is bypassed,
`forge_redis_errors_total` counts every absorbed failure so operators
get paged instead of clients getting 500s. Metering is a convenience,
not a serving dependency. The opposite (fail closed) is right only when
quotas are hard billing/contractual guarantees — then serving unmetered
is the incident. Either way the point is: it's a deliberate choice, and
the code and docs state it. (History note: v1.0 shipped fail-closed by
accident — an external review caught it; the fix + regression test is
in git history.)

## Interview drill topics
"Walk me through a request when the GPU node just died." /
"Why not put the queue in front of auth?" / "Design per-second fair
sharing between tenants on top of this."
