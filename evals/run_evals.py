#!/usr/bin/env python3
"""Forge eval harness — the deploy gate.

Runs the eval set against a live gateway and exits non-zero if any
platform check fails, which is what makes CI publishing (and therefore
ArgoCD rollout) conditional on evals passing.

Platform checks (work against mock backends, run in CI):
    auth-rejected        requests without a key get 401
    models-alias         /v1/models advertises the public alias
    prompt/<id>          each eval prompt: 200, non-empty answer, within SLO
    cache-dedup          identical deterministic request served from cache
    streaming            SSE stream yields chunks and [DONE]
    failover-drill       (--drill) inject primary failure -> 200 from fallback

Usage:
    python run_evals.py --url http://localhost:8080 --key forge-loadtest-localdev-key
    python run_evals.py --drill --mock-control http://localhost:8001
"""
import argparse
import asyncio
import json
import pathlib
import sys
import time

import httpx
import yaml

HERE = pathlib.Path(__file__).parent


class Report:
    def __init__(self):
        self.results = []

    def add(self, name: str, ok: bool, detail: str = ""):
        self.results.append({"check": name, "ok": ok, "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL':>4}  {name:<24} {detail}")

    @property
    def failed(self):
        return [r for r in self.results if not r["ok"]]


def chat_body(prompt: str, **kw) -> dict:
    return {
        "model": "forge-default",
        "messages": [{"role": "user", "content": prompt}],
        # Smoke-test sized: on a single T4 serving a 3B model, long
        # completions under a 20-prompt stampede breach any honest SLO —
        # evals measure per-request latency at realistic concurrency.
        "max_tokens": 64,
        **kw,
    }


async def run(args) -> int:
    spec = yaml.safe_load((HERE / "prompts.yaml").read_text())
    slo = float(spec["slo_latency_s"])
    report = Report()
    headers = {"Authorization": f"Bearer {args.key}"}

    async with httpx.AsyncClient(base_url=args.url, timeout=30) as client:
        # auth is enforced
        r = await client.post("/v1/chat/completions", json=chat_body("hi"))
        report.add("auth-rejected", r.status_code == 401, f"got {r.status_code}")

        # model alias is advertised
        r = await client.get("/v1/models")
        ids = [m["id"] for m in r.json().get("data", [])]
        report.add("models-alias", "forge-default" in ids, f"models={ids}")

        # the prompt set, at bounded concurrency (a 20-way burst on a
        # single-GPU backend measures queueing, not serving)
        gate = asyncio.Semaphore(args.max_concurrency)

        async def one(p):
            async with gate:
                started = time.monotonic()
                resp = await client.post(
                    "/v1/chat/completions", json=chat_body(p["prompt"]), headers=headers
                )
            elapsed = time.monotonic() - started
            if resp.status_code != 200:
                return p["id"], False, f"HTTP {resp.status_code}"
            content = resp.json()["choices"][0]["message"]["content"]
            if not content.strip():
                return p["id"], False, "empty answer"
            if elapsed > slo:
                return p["id"], False, f"latency {elapsed:.1f}s > SLO {slo}s"
            backend = resp.headers.get("x-forge-backend", "?")
            return p["id"], True, f"{elapsed:.2f}s via {backend}"

        for coro in asyncio.as_completed([one(p) for p in spec["prompts"]]):
            pid, ok, detail = await coro
            report.add(f"prompt/{pid}", ok, detail)

        # deterministic requests are deduplicated by the cache
        body = chat_body("cache probe: state the city motto.", temperature=0)
        await client.post("/v1/chat/completions", json=body, headers=headers)
        r = await client.post("/v1/chat/completions", json=body, headers=headers)
        report.add(
            "cache-dedup",
            r.headers.get("x-forge-cache") == "hit",
            f"x-forge-cache={r.headers.get('x-forge-cache')}",
        )

        # streaming works end to end
        r = await client.post(
            "/v1/chat/completions",
            json=chat_body("stream a short greeting", stream=True),
            headers=headers,
        )
        ok = r.status_code == 200 and "data:" in r.text and "[DONE]" in r.text
        report.add("streaming", ok, f"HTTP {r.status_code}, {len(r.text)} bytes")

        # fairness: one tenant flooding must not affect another tenant
        if args.second_key:
            flood = [
                asyncio.create_task(
                    client.post(
                        "/v1/chat/completions",
                        json=chat_body(f"flood {i}"),
                        headers=headers,
                    )
                )
                for i in range(40)
            ]
            await asyncio.sleep(0.3)  # let the flood saturate the queue
            ok = 0
            for i in range(5):
                r = await client.post(
                    "/v1/chat/completions",
                    json=chat_body(f"victim tenant {i}"),
                    headers={"Authorization": f"Bearer {args.second_key}"},
                )
                ok += r.status_code == 200
            flood_results = await asyncio.gather(*flood, return_exceptions=True)
            shed = sum(
                1 for r in flood_results
                if not isinstance(r, Exception) and r.status_code == 429
            )
            report.add(
                "fairness-isolation",
                ok == 5,
                f"{ok}/5 ok for second tenant while flooder had {shed} shed",
            )

        # failover drill: break the primary, prove clients don't notice
        if args.drill:
            if not args.mock_control:
                report.add("failover-drill", False, "--mock-control required")
            else:
                async with httpx.AsyncClient(timeout=10) as ctl:
                    await ctl.post(f"{args.mock_control}/control", json={"fail": True})
                    try:
                        r = await client.post(
                            "/v1/chat/completions",
                            json=chat_body("drill during outage"),
                            headers=headers,
                        )
                        backend = r.headers.get("x-forge-backend")
                        report.add(
                            "failover-drill",
                            r.status_code == 200 and backend != "vllm",
                            f"HTTP {r.status_code} via {backend}",
                        )
                    finally:
                        await ctl.post(f"{args.mock_control}/control", json={"fail": False})

    total, failed = len(report.results), len(report.failed)
    print(f"\n{total - failed}/{total} checks passed")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report.results, indent=2))
    if failed:
        print("DEPLOY GATE: FAIL — publishing is blocked")
        return 1
    print("DEPLOY GATE: PASS")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--key", default="forge-loadtest-localdev-key")
    parser.add_argument("--max-concurrency", type=int, default=4,
                        help="concurrent eval prompts (per-request latency, not queue latency)")
    parser.add_argument("--second-key", default="forge-demo-localdev-key",
                        help="second tenant's key for the fairness check ('' to skip)")
    parser.add_argument("--drill", action="store_true")
    parser.add_argument("--mock-control", default="http://localhost:8001",
                        help="mock-vllm control endpoint for the failover drill")
    parser.add_argument("--json", help="write results to this JSON file")
    sys.exit(asyncio.run(run(parser.parse_args())))
