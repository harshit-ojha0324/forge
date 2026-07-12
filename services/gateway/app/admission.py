"""Admission control: bounded concurrency with per-tenant fair queueing.

Three-level protection:

  1. `max_concurrency` requests run against backends at once, globally —
     that's the resource actually being protected (GPU batch capacity).
  2. Each tenant gets its OWN bounded waiting queue (`max_waiting` per
     tenant). A tenant that floods sheds 429s for itself only; other
     tenants' queues are untouched. This is the noisy-neighbor boundary.
  3. Freed slots are granted across waiting tenants by smooth weighted
     round-robin (the nginx algorithm): a tenant with weight 2 gets ~2×
     the slots of a weight-1 tenant under contention, and the sequence
     is interleaved (a b a, not a a b) so nobody observes bursts.

A waiter that can't get a slot within `wait_timeout_s` gets 503 — the
client's own deadline has likely passed by then anyway.

Fairness scope (honest): this shares *slots*, not tokens — a tenant
sending 4k-token prompts still occupies its slots for longer. Token-cost
aware scheduling would need estimated-cost deficit accounting on top.
"""
import asyncio
from collections import deque
from contextlib import asynccontextmanager

from . import metrics
from .errors import QueueFull, QueueWaitTimeout

DEFAULT_TENANT = "anonymous"


class _TenantQueue:
    __slots__ = ("waiters", "weight", "current")

    def __init__(self, weight: int):
        self.waiters: deque[asyncio.Future] = deque()
        self.weight = weight
        self.current = 0  # smooth-WRR accumulator

    def pending(self) -> int:
        return sum(1 for f in self.waiters if not f.done())


class AdmissionController:
    def __init__(self, max_concurrency: int, max_waiting: int, wait_timeout_s: float):
        self._max_concurrency = max_concurrency
        self._max_waiting = max_waiting  # per tenant
        self._wait_timeout_s = wait_timeout_s
        self._queues: dict[str, _TenantQueue] = {}
        self.in_flight = 0

    @property
    def waiting(self) -> int:
        return sum(q.pending() for q in self._queues.values())

    def _has_waiters(self) -> bool:
        return any(q.pending() for q in self._queues.values())

    async def acquire(self, tenant: str = DEFAULT_TENANT, weight: int = 1) -> None:
        # Fast path — capacity free and nobody queued ahead. Must not
        # yield to the event loop, or burst traffic reads phantom queues.
        if self.in_flight < self._max_concurrency and not self._has_waiters():
            self.in_flight += 1
            metrics.IN_FLIGHT.set(self.in_flight)
            return

        queue = self._queues.get(tenant)
        if queue is None:
            queue = self._queues[tenant] = _TenantQueue(max(weight, 1))
        queue.weight = max(weight, 1)  # config changes take effect live

        if queue.pending() >= self._max_waiting:
            raise QueueFull(
                f"tenant '{tenant}' queue full ({self._max_waiting} waiting); retry later"
            )

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        queue.waiters.append(future)
        metrics.QUEUE_WAITING.labels(tenant).inc()
        try:
            try:
                await asyncio.wait_for(future, self._wait_timeout_s)
            except asyncio.TimeoutError:
                if future.done() and not future.cancelled():
                    return  # granted at the same instant the timeout fired
                raise QueueWaitTimeout(
                    f"no capacity within {self._wait_timeout_s}s"
                ) from None
        finally:
            metrics.QUEUE_WAITING.labels(tenant).dec()
        # Granted: the releasing request's slot transferred to us —
        # in_flight was never decremented, so nothing to increment.

    def release(self) -> None:
        granted = self._grant_next()
        if not granted:
            self.in_flight -= 1
        metrics.IN_FLIGHT.set(self.in_flight)

    def _grant_next(self) -> bool:
        """Pick the next waiter by smooth weighted round-robin; True if a
        slot was handed off (in_flight stays constant)."""
        while True:
            active = [
                (name, q) for name, q in self._queues.items() if q.pending()
            ]
            if not active:
                # Drop empty tenant queues so the dict doesn't grow forever.
                for name in [n for n, q in self._queues.items() if not q.waiters]:
                    del self._queues[name]
                return False

            total_weight = sum(q.weight for _, q in active)
            best = None
            for _, q in active:
                q.current += q.weight
                if best is None or q.current > best.current:
                    best = q
            best.current -= total_weight

            # Grant to the tenant's oldest live waiter (skip timed-out ones).
            while best.waiters:
                future = best.waiters.popleft()
                if not future.done():
                    future.set_result(None)
                    return True
            # All this tenant's waiters were dead — pick again.

    @asynccontextmanager
    async def slot(self, tenant: str = DEFAULT_TENANT, weight: int = 1):
        """Context-managed slot for unary requests. Streaming requests use
        acquire()/release() directly because the slot must outlive the
        route handler (it is released when the stream finishes)."""
        await self.acquire(tenant, weight)
        try:
            yield
        finally:
            self.release()
