import asyncio
import json

import httpx
import respx

from conftest import AUTH, AUTH_BETA, chat_request, completion_body

PRIMARY = "http://primary.test/v1/chat/completions"
FALLBACK = "http://fallback.test/v1/chat/completions"


async def test_rejects_missing_api_key(client):
    r = await client.post("/v1/chat/completions", json=chat_request())
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "invalid_api_key"


@respx.mock
async def test_unary_happy_path_serves_from_primary(client):
    route = respx.post(PRIMARY).respond(json=completion_body())
    r = await client.post("/v1/chat/completions", json=chat_request(), headers=AUTH)
    assert r.status_code == 200
    assert r.headers["x-forge-backend"] == "vllm"
    assert r.json()["choices"][0]["message"]["content"] == "hello from model"
    # gateway rewrote the public alias to the backend's real model
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "test-primary-model"

    usage = await client.get("/v1/usage", headers=AUTH)
    assert usage.json()["used_tokens"] == 30


@respx.mock
async def test_failover_to_gemini_on_primary_error(client):
    respx.post(PRIMARY).respond(status_code=500)
    respx.post(FALLBACK).respond(json=completion_body(content="gemini says hi"))
    r = await client.post("/v1/chat/completions", json=chat_request(), headers=AUTH)
    assert r.status_code == 200  # client never sees the primary failure
    assert r.headers["x-forge-backend"] == "gemini"


@respx.mock
async def test_failover_on_connect_error(client):
    respx.post(PRIMARY).mock(side_effect=httpx.ConnectError("refused"))
    respx.post(FALLBACK).respond(json=completion_body())
    r = await client.post("/v1/chat/completions", json=chat_request(), headers=AUTH)
    assert r.status_code == 200
    assert r.headers["x-forge-backend"] == "gemini"


@respx.mock
async def test_breaker_opens_then_recovers(client):
    primary = respx.post(PRIMARY).respond(status_code=500)
    respx.post(FALLBACK).respond(json=completion_body())

    # threshold is 2: two failing requests trip the breaker
    for _ in range(2):
        r = await client.post("/v1/chat/completions", json=chat_request(), headers=AUTH)
        assert r.status_code == 200
    assert primary.call_count == 2

    # breaker OPEN: primary is not attempted at all
    r = await client.post("/v1/chat/completions", json=chat_request(), headers=AUTH)
    assert r.headers["x-forge-backend"] == "gemini"
    assert primary.call_count == 2

    # after the cooldown, one probe goes through and closes the breaker
    await asyncio.sleep(0.25)
    primary.respond(json=completion_body(content="primary recovered"))
    r = await client.post("/v1/chat/completions", json=chat_request(), headers=AUTH)
    assert r.headers["x-forge-backend"] == "vllm"
    assert r.json()["choices"][0]["message"]["content"] == "primary recovered"


@respx.mock
async def test_quota_exhaustion_returns_429(client):
    respx.post(PRIMARY).respond(
        json=completion_body(prompt_tokens=30, completion_tokens=30)
    )
    r = await client.post("/v1/chat/completions", json=chat_request(), headers=AUTH_BETA)
    assert r.status_code == 200  # 60 tokens consumed of a 50-token quota

    r = await client.post("/v1/chat/completions", json=chat_request(), headers=AUTH_BETA)
    assert r.status_code == 429
    assert r.json()["error"]["type"] == "quota_exhausted"
    assert "Retry-After" in r.headers


@respx.mock
async def test_deterministic_requests_are_cached(client):
    route = respx.post(PRIMARY).respond(json=completion_body())
    body = chat_request(temperature=0)

    r1 = await client.post("/v1/chat/completions", json=body, headers=AUTH)
    assert r1.headers["x-forge-cache"] == "miss"
    r2 = await client.post("/v1/chat/completions", json=body, headers=AUTH)
    assert r2.headers["x-forge-cache"] == "hit"
    assert r2.headers["x-forge-backend"] == "cache"
    assert route.call_count == 1
    assert r1.json()["choices"] == r2.json()["choices"]


@respx.mock
async def test_sampling_requests_are_not_cached(client):
    route = respx.post(PRIMARY).respond(json=completion_body())
    body = chat_request(temperature=0.9)
    await client.post("/v1/chat/completions", json=body, headers=AUTH)
    await client.post("/v1/chat/completions", json=body, headers=AUTH)
    assert route.call_count == 2


@respx.mock
async def test_queue_sheds_load_beyond_capacity(client):
    async def slow_response(request):
        await asyncio.sleep(0.3)
        return httpx.Response(200, json=completion_body())

    respx.post(PRIMARY).mock(side_effect=slow_response)

    # capacity 2 + queue 2 => firing 6 concurrently must shed at least 2
    tasks = [
        client.post("/v1/chat/completions", json=chat_request(), headers=AUTH)
        for _ in range(6)
    ]
    responses = await asyncio.gather(*tasks)
    codes = sorted(r.status_code for r in responses)
    assert codes.count(200) == 4
    assert codes.count(429) == 2
    shed = [r for r in responses if r.status_code == 429]
    assert all(r.json()["error"]["type"] == "queue_full" for r in shed)
    assert all("Retry-After" in r.headers for r in shed)


@respx.mock
async def test_upstream_4xx_passes_through_without_failover(client):
    fallback = respx.post(FALLBACK).respond(json=completion_body())
    respx.post(PRIMARY).respond(
        status_code=400, json={"error": {"message": "context too long", "type": "bad_request"}}
    )
    r = await client.post("/v1/chat/completions", json=chat_request(), headers=AUTH)
    assert r.status_code == 400
    assert fallback.call_count == 0  # 4xx is the caller's fault; no failover


@respx.mock
async def test_all_backends_down_returns_502(client):
    respx.post(PRIMARY).respond(status_code=500)
    respx.post(FALLBACK).mock(side_effect=httpx.ConnectError("down"))
    r = await client.post("/v1/chat/completions", json=chat_request(), headers=AUTH)
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "all_backends_failed"


def sse_body(chunks, usage=None):
    lines = []
    for text in chunks:
        payload = {"choices": [{"index": 0, "delta": {"content": text}}]}
        lines.append(f"data: {json.dumps(payload)}\n\n")
    if usage:
        lines.append(f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


@respx.mock
async def test_streaming_passthrough(client):
    respx.post(PRIMARY).respond(
        content=sse_body(
            ["Hel", "lo"], usage={"prompt_tokens": 5, "completion_tokens": 2}
        ),
        headers={"content-type": "text/event-stream"},
    )
    r = await client.post(
        "/v1/chat/completions", json=chat_request(stream=True), headers=AUTH
    )
    assert r.status_code == 200
    assert r.headers["x-forge-backend"] == "vllm"
    assert "Hel" in r.text and "[DONE]" in r.text

    await asyncio.sleep(0.05)  # let the finalize task record usage
    usage = await client.get("/v1/usage", headers=AUTH)
    assert usage.json()["used_tokens"] == 7


@respx.mock
async def test_streaming_fails_over_before_first_token(client):
    respx.post(PRIMARY).respond(status_code=503)
    respx.post(FALLBACK).respond(
        content=sse_body(["fallback stream"]),
        headers={"content-type": "text/event-stream"},
    )
    r = await client.post(
        "/v1/chat/completions", json=chat_request(stream=True), headers=AUTH
    )
    assert r.status_code == 200
    assert r.headers["x-forge-backend"] == "gemini"
    assert "fallback stream" in r.text


async def test_models_endpoint_advertises_alias(client):
    r = await client.get("/v1/models")
    assert r.json()["data"][0]["id"] == "forge-default"


async def test_metrics_endpoint_exposes_forge_metrics(client):
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "forge_requests_total" in r.text
