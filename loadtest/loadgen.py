#!/usr/bin/env python3
"""Async load generator for the Forge gateway.

Prints one line per second with outcome counts and which backend served
the traffic — during the failover demo you watch the vllm column drop to
zero and gemini take over while the 5xx column stays at 0.

Usage:
    python loadgen.py --url http://localhost:8080 --key forge-loadtest-localdev-key \
        --concurrency 12 --duration 60
"""
import argparse
import asyncio
import collections
import time

import httpx

PROMPTS = [
    "Summarize today's traffic conditions downtown.",
    "Which bus routes are delayed right now?",
    "Draft a maintenance alert for sensor cluster 7.",
    "What is the air quality index near the river district?",
    "Plan the fastest route from the depot to city hall.",
    "Explain why intersection 12 is congested.",
]


class Stats:
    def __init__(self):
        self.window = collections.Counter()
        self.total = collections.Counter()
        self.latencies = []

    def record(self, key: str, latency: float | None = None):
        self.window[key] += 1
        self.total[key] += 1
        if latency is not None:
            self.latencies.append(latency)

    def flush_window(self) -> collections.Counter:
        w, self.window = self.window, collections.Counter()
        return w


async def worker(client: httpx.AsyncClient, args, stats: Stats, stop: asyncio.Event, i: int):
    n = 0
    while not stop.is_set():
        body = {
            "model": "forge-default",
            "messages": [{"role": "user", "content": f"[w{i}#{n}] {PROMPTS[n % len(PROMPTS)]}"}],
            "max_tokens": 64,
        }
        n += 1
        started = time.monotonic()
        try:
            r = await client.post(
                "/v1/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {args.key}"},
            )
        except httpx.HTTPError:
            stats.record("transport_error")
            continue
        latency = time.monotonic() - started
        if r.status_code == 200:
            stats.record(f"ok:{r.headers.get('x-forge-backend', '?')}", latency)
        elif r.status_code == 429:
            stats.record("429")
            await asyncio.sleep(0.2)
        elif r.status_code >= 500:
            stats.record("5xx")
        else:
            stats.record(f"http_{r.status_code}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--key", default="forge-loadtest-localdev-key")
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--duration", type=int, default=60)
    args = parser.parse_args()

    stats = Stats()
    stop = asyncio.Event()
    async with httpx.AsyncClient(base_url=args.url, timeout=30) as client:
        workers = [
            asyncio.create_task(worker(client, args, stats, stop, i))
            for i in range(args.concurrency)
        ]
        print(f"{'t':>4} {'ok/vllm':>8} {'ok/gemini':>10} {'ok/cache':>9} "
              f"{'429':>5} {'5xx':>5} {'other':>6}")
        for t in range(args.duration):
            await asyncio.sleep(1)
            w = stats.flush_window()
            other = sum(
                v for k, v in w.items()
                if k not in ("ok:vllm", "ok:gemini", "ok:cache", "429", "5xx")
            )
            print(f"{t + 1:>4} {w['ok:vllm']:>8} {w['ok:gemini']:>10} {w['ok:cache']:>9} "
                  f"{w['429']:>5} {w['5xx']:>5} {other:>6}")
        stop.set()
        for task in workers:
            task.cancel()

    total_ok = sum(v for k, v in stats.total.items() if k.startswith("ok:"))
    total_5xx = stats.total["5xx"] + stats.total["transport_error"]
    lat = sorted(stats.latencies)
    print("\n=== summary ===")
    for key in sorted(stats.total):
        print(f"  {key:>16}: {stats.total[key]}")
    if lat:
        print(f"  p50 latency: {lat[len(lat) // 2] * 1000:.0f} ms")
        print(f"  p95 latency: {lat[int(len(lat) * 0.95)] * 1000:.0f} ms")
    print(f"\n  client-visible failures: {total_5xx} "
          f"({'ZERO 5xx — failover held' if total_5xx == 0 else 'INVESTIGATE'})")
    print(f"  successful requests: {total_ok}")


if __name__ == "__main__":
    asyncio.run(main())
