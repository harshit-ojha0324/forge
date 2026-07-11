# Cost breakdown

*Prices are us-central1 list prices as of mid-2026; check current rates.
The design goal: idle cost near zero, demo cost measured in cents.*

## Steady state (what runs 24/7 if you leave it up)

| Item | Spec | ~$/hr | ~$/mo |
|---|---|---|---|
| GKE control plane | zonal | $0.10 | covered by GKE free-tier credit ($74.40/mo, one zonal cluster) |
| services pool | 1–2 × e2-standard-2 **spot** | ~$0.02/node | $15–30 |
| gpu-t4 pool | **scaled to zero** | $0 | $0 |
| Cloud NAT | gateway + data | ~$0.045 + data | ~$32/mo if left up ⚠ |
| Artifact Registry | few GB | — | < $1 |

⚠ Cloud NAT is the sneaky one, not the GPU. Two options: tear down the
whole stack between sessions (`terraform destroy` — everything is code,
rebuild is ~15 min) or accept it for the active week and destroy after.

## When the GPU is on (demos and dev sessions only)

| Item | Spec | ~$/hr |
|---|---|---|
| n1-standard-4 (spot) | 4 vCPU / 15 GB | ~$0.04 |
| T4 GPU (spot) | 16 GB | ~$0.11–0.16 |
| **total GPU-on cost** | | **~$0.15–0.20/hr** |

A 2-hour demo session ≈ **35–40¢**. The pool autoscales 0→1 when the
vLLM pod schedules and back to 0 when the app is deleted — turning the
GPU on/off is an ArgoCD sync/delete, not a console operation.

## Fallback (Gemini) cost

gemini-2.0-flash-class pricing is fractions of a cent per request at this
project's token sizes. During a failover incident the per-tenant token
dashboard quantifies exactly what shifted to the paid API.

## Realistic project total

- Local development (stage 1): **$0** — the entire platform runs on
  docker-compose.
- Cloud stages (2–5) with disciplined teardown, ~10–15 hours of cluster
  time + a few GPU hours: **$10–25 total**.
- Leaving everything up for a month instead: ~$80–100 — don't.

## Cost discipline checklist

- [ ] `terraform destroy` at the end of every session (state is in git + GCS)
- [ ] vLLM app deleted (GPU pool at 0) whenever not actively demoing
- [ ] Billing budget alert at $25 and $50 (docs/gcp-setup.md sets it up)
- [ ] Spot everywhere; nothing in this lab justifies on-demand
