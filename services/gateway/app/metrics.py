"""Prometheus metrics. Names are the contract with the Grafana dashboards
and alert rules in observability/ — change them together."""
from prometheus_client import Counter, Gauge, Histogram

LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 30, 60)
TTFT_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8)

REQUESTS = Counter(
    "forge_requests_total",
    "Chat completion requests by tenant, serving backend and outcome",
    ["tenant", "backend", "outcome"],
)
LATENCY = Histogram(
    "forge_request_duration_seconds",
    "End-to-end request latency",
    ["backend"],
    buckets=LATENCY_BUCKETS,
)
TTFT = Histogram(
    "forge_ttft_seconds",
    "Time to first streamed token",
    ["backend"],
    buckets=TTFT_BUCKETS,
)
TOKENS = Counter(
    "forge_tokens_total",
    "Tokens processed per tenant",
    ["tenant", "direction"],  # direction: prompt | completion
)
QUEUE_WAITING = Gauge(
    "forge_queue_waiting",
    "Requests waiting for a capacity slot, per tenant (fair-queueing view)",
    ["tenant"],
)
IN_FLIGHT = Gauge("forge_inflight_requests", "Requests currently executing upstream")
CACHE_EVENTS = Counter(
    "forge_cache_events_total", "Response cache activity", ["result"]  # hit|miss|store
)
BREAKER_STATE = Gauge(
    "forge_breaker_state", "Primary-backend breaker: 0=closed 1=half-open 2=open"
)
SHED = Counter(
    "forge_shed_total", "Requests rejected before reaching a backend", ["reason"]
)
FAILOVERS = Counter(
    "forge_failover_total",
    "Requests that fell over to another backend",
    ["from_backend", "to_backend"],
)
REDIS_ERRORS = Counter(
    "forge_redis_errors_total",
    "Redis failures absorbed by fail-open paths (quota/cache degraded, serving unaffected)",
    ["op"],
)
