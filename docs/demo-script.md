# The 2-minute demo video — shot list

Record once with the local stack (record again on GKE later if you want
the `kubectl delete pod` version — the script is identical, the kill
command changes).

**Setup before recording:** `make up`, open Grafana
`http://localhost:3000/d/forge-overview` (dark theme), terminal beside it.
Let one `make loadgen` run finish so the dashboard has history.

---

**0:00–0:15 — the claim.**
Screen: architecture diagram from the README.
Say: "Forge is a multi-tenant inference gateway: one OpenAI-compatible
endpoint, self-hosted vLLM as the primary backend, Gemini as fallback,
per-tenant quotas, and a circuit breaker in between. I'm going to kill
the primary model server under load, live."

**0:15–0:35 — steady state.**
Screen: split — loadgen ticking (`ok/vllm` ~20/s), Grafana requests-by-
backend all `vllm`, breaker stat green CLOSED.
Say: "Load generator is pushing 20 requests a second, all served by vLLM.
Breaker closed, error rate zero."

**0:35–1:10 — the kill.**
Terminal: `docker compose stop -t 0 mock-vllm`   (GKE: `kubectl -n forge delete pod -l app.kubernetes.io/name=vllm`)
Screen: loadgen columns flip — `ok/vllm` → 0, `ok/gemini` takes over; 5xx
column stays 0. Grafana: breaker stat flips to red OPEN, backend area
chart swaps color, client-error stat stays 0%.
Say: "Primary's dead. Three failed requests tripped the breaker, and
every request after that routes straight to the fallback — nobody pays
a timeout on a backend we know is down. The 5xx column: still zero."

**1:10–1:40 — recovery.**
Terminal: `docker compose start mock-vllm`
Screen: after the cooldown, one probe → breaker CLOSED → traffic returns
to vllm.
Say: "Primary's back. The breaker half-opens, sends one probe request —
not a thundering herd — and on success, closes. Traffic's home."

**1:40–2:00 — the receipts.**
Screen: loadgen summary (`client-visible failures: 0 — ZERO 5xx`), then
the eval gate: `make evals` → `DEPLOY GATE: PASS`.
Say: "Full outage and recovery of the primary model backend: zero
client-visible errors. And this exact failover drill runs in CI — images
don't publish unless it passes."

---

Upload as `docs/demo.mp4` or a Loom/YouTube-unlisted link in the README.
