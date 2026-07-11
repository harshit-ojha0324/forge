"""Circuit breaker guarding the primary (self-hosted vLLM) backend.

State machine:

    CLOSED --(N consecutive failures)--> OPEN
    OPEN   --(cooldown elapsed)-------> HALF_OPEN
    HALF_OPEN --(probe succeeds)------> CLOSED
    HALF_OPEN --(probe fails)---------> OPEN (cooldown restarts)

While OPEN, requests skip the primary entirely and go straight to the
fallback — no per-request timeout is paid on a backend we already know
is down. HALF_OPEN lets exactly one in-flight probe through; everyone
else keeps using the fallback until the probe reports back.
"""
import asyncio
import enum
import time


class BreakerState(enum.IntEnum):
    CLOSED = 0
    HALF_OPEN = 1
    OPEN = 2


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_s: float = 15.0):
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> BreakerState:
        return self._state

    async def allow_primary(self) -> bool:
        """Decide whether this request may attempt the primary backend."""
        async with self._lock:
            if self._state == BreakerState.CLOSED:
                return True
            if self._state == BreakerState.OPEN:
                if time.monotonic() - self._opened_at >= self.cooldown_s:
                    self._state = BreakerState.HALF_OPEN
                    self._probe_in_flight = True
                    return True  # this request becomes the probe
                return False
            # HALF_OPEN: only one probe at a time.
            if not self._probe_in_flight:
                self._probe_in_flight = True
                return True
            return False

    async def record_success(self) -> None:
        async with self._lock:
            self._consecutive_failures = 0
            self._probe_in_flight = False
            self._state = BreakerState.CLOSED

    async def record_failure(self) -> None:
        async with self._lock:
            self._consecutive_failures += 1
            if self._state == BreakerState.HALF_OPEN:
                self._trip()
            elif (
                self._state == BreakerState.CLOSED
                and self._consecutive_failures >= self.failure_threshold
            ):
                self._trip()

    def _trip(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        self._probe_in_flight = False
