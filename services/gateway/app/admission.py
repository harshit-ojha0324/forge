"""Admission control: bounded concurrency with a bounded wait queue.

Two-level protection:
  1. `max_concurrency` requests run against backends at once.
  2. Up to `max_waiting` requests may wait for a slot. Beyond that we
     shed load immediately with 429 rather than building an unbounded
     backlog (queueing past capacity only adds latency, never throughput).
  3. A waiter that cannot get a slot within `wait_timeout_s` gets 503 —
     the client's own deadline has likely passed by then anyway.

Implemented with an explicit counter + FIFO of waiter futures instead of
an asyncio.Semaphore: the fast path must admit without yielding to the
event loop, otherwise a burst of requests briefly counts slot-holders as
"waiting" and sheds load that capacity could have served.
"""
import asyncio
from collections import deque
from contextlib import asynccontextmanager

from .errors import QueueFull, QueueWaitTimeout


class AdmissionController:
    def __init__(self, max_concurrency: int, max_waiting: int, wait_timeout_s: float):
        self._max_concurrency = max_concurrency
        self._max_waiting = max_waiting
        self._wait_timeout_s = wait_timeout_s
        self._waiters: deque[asyncio.Future] = deque()
        self.waiting = 0
        self.in_flight = 0

    async def acquire(self) -> None:
        # Fast path: free slot and nobody queued ahead (FIFO fairness).
        # No await between check and increment, so this is race-free on
        # a single event loop.
        if self.in_flight < self._max_concurrency and not self._waiters:
            self.in_flight += 1
            return

        if self.waiting >= self._max_waiting:
            raise QueueFull(f"queue full ({self._max_waiting} waiting); retry later")

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        self.waiting += 1
        try:
            await asyncio.wait_for(fut, self._wait_timeout_s)
        except asyncio.TimeoutError:
            if fut.done() and not fut.cancelled():
                return  # slot granted the instant the timeout fired
            raise QueueWaitTimeout(
                f"no capacity within {self._wait_timeout_s}s"
            ) from None
        finally:
            self.waiting -= 1
        # Slot ownership transferred by release(); in_flight already counts us.

    def release(self) -> None:
        while self._waiters:
            fut = self._waiters.popleft()
            if not fut.done():  # skip waiters that timed out
                fut.set_result(None)
                return  # in_flight transfers to the woken waiter
        self.in_flight -= 1

    @asynccontextmanager
    async def slot(self):
        """Context-managed slot for unary requests. Streaming requests use
        acquire()/release() directly because the slot must outlive the
        route handler (it is released when the stream finishes)."""
        await self.acquire()
        try:
            yield
        finally:
            self.release()
