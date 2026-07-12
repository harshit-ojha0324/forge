import asyncio

import pytest

from app.admission import AdmissionController
from app.breaker import BreakerState, CircuitBreaker
from app.cache import cache_key, is_cacheable
from app.errors import QueueFull, QueueWaitTimeout


async def test_breaker_state_machine():
    breaker = CircuitBreaker(failure_threshold=2, cooldown_s=0.1)
    assert breaker.state == BreakerState.CLOSED

    await breaker.record_failure()
    assert breaker.state == BreakerState.CLOSED  # below threshold
    await breaker.record_failure()
    assert breaker.state == BreakerState.OPEN

    assert not await breaker.allow_primary()  # cooldown not elapsed
    await asyncio.sleep(0.12)
    assert await breaker.allow_primary()  # becomes the half-open probe
    assert breaker.state == BreakerState.HALF_OPEN
    assert not await breaker.allow_primary()  # only one probe at a time

    await breaker.record_success()
    assert breaker.state == BreakerState.CLOSED


async def test_breaker_reopens_on_failed_probe():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=0.1)
    await breaker.record_failure()
    assert breaker.state == BreakerState.OPEN
    await asyncio.sleep(0.12)
    assert await breaker.allow_primary()
    await breaker.record_failure()  # probe failed
    assert breaker.state == BreakerState.OPEN


async def test_success_resets_consecutive_failures():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_s=1)
    await breaker.record_failure()
    await breaker.record_failure()
    await breaker.record_success()
    await breaker.record_failure()
    await breaker.record_failure()
    assert breaker.state == BreakerState.CLOSED


async def test_admission_sheds_when_queue_full():
    ac = AdmissionController(max_concurrency=1, max_waiting=1, wait_timeout_s=5)
    await ac.acquire("a")  # occupies the only slot

    waiter = asyncio.create_task(ac.acquire("a"))  # fills tenant a's queue
    await asyncio.sleep(0.01)
    with pytest.raises(QueueFull):
        await ac.acquire("a")  # a's queue full -> immediate shed for a

    ac.release()
    await waiter
    ac.release()


async def test_admission_times_out_waiting():
    ac = AdmissionController(max_concurrency=1, max_waiting=2, wait_timeout_s=0.05)
    await ac.acquire("a")
    with pytest.raises(QueueWaitTimeout):
        await ac.acquire("a")
    ac.release()


def test_cache_key_ignores_irrelevant_fields():
    a = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "temperature": 0}
    b = dict(a, user="someone-else", metadata={"x": 1})
    assert cache_key(a) == cache_key(b)


def test_cache_key_changes_with_messages():
    a = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "temperature": 0}
    b = dict(a, messages=[{"role": "user", "content": "bye"}])
    assert cache_key(a) != cache_key(b)


def test_only_deterministic_nonstreaming_is_cacheable():
    assert is_cacheable({"temperature": 0})
    assert not is_cacheable({"temperature": 0.7})
    assert not is_cacheable({"temperature": 0, "stream": True})
    assert not is_cacheable({})  # temperature defaults to 1.0


async def test_success_while_open_is_stale_evidence():
    """A slow pre-trip request completing during OPEN must not slam the
    breaker shut — recovery goes through the half-open probe."""
    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=10)
    await breaker.record_failure()
    assert breaker.state == BreakerState.OPEN
    await breaker.record_success()  # straggler from before the outage
    assert breaker.state == BreakerState.OPEN


async def test_flooding_tenant_does_not_shed_others():
    """The noisy-neighbor boundary: tenant a fills its own queue and gets
    shed; tenant b still queues and gets served."""
    ac = AdmissionController(max_concurrency=1, max_waiting=2, wait_timeout_s=5)
    await ac.acquire("a")  # slot taken

    a_waiters = [asyncio.create_task(ac.acquire("a")) for _ in range(2)]
    await asyncio.sleep(0.01)
    with pytest.raises(QueueFull):
        await ac.acquire("a")  # a is at its cap

    b_waiter = asyncio.create_task(ac.acquire("b"))  # b queues fine
    await asyncio.sleep(0.01)
    assert not b_waiter.done()

    for _ in range(3):  # drain: every waiter eventually gets the slot
        ac.release()
        await asyncio.sleep(0.01)
    await b_waiter
    for t in a_waiters:
        await t
    ac.release()


async def test_weighted_round_robin_grant_ratio():
    """Weight 2 tenant gets ~2x the grants, interleaved (a,b,a pattern),
    per smooth weighted round-robin."""
    ac = AdmissionController(max_concurrency=1, max_waiting=10, wait_timeout_s=5)
    await ac.acquire("seed")

    order = []

    async def waiter(name, weight):
        await ac.acquire(name, weight)
        order.append(name)

    tasks = [asyncio.create_task(waiter("a", 2)) for _ in range(4)]
    await asyncio.sleep(0.01)  # a's waiters enqueue first...
    tasks += [asyncio.create_task(waiter("b", 1)) for _ in range(2)]
    await asyncio.sleep(0.01)

    for _ in range(6):
        ac.release()  # each release grants the slot onward
        await asyncio.sleep(0.01)

    for t in tasks:
        await t
    ac.release()

    assert order[:3] == ["a", "b", "a"]  # interleaved, not a-burst-first
    assert order.count("a") == 4 and order.count("b") == 2
