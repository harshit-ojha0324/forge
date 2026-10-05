"""Request orchestration: auth -> quota -> cache -> admission -> breaker-routed
backend call (with failover) -> metering.

Failover contract: a request only fails over while it is still safe to do
so. Unary requests can fail over at any point before a response is
returned. Streaming requests can fail over until the upstream accepts the
request (status < 500); once bytes have been sent to the client the
stream cannot be replayed on another backend and a mid-stream failure
terminates the response (and counts against the breaker).
"""
import asyncio
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import metrics
from .backends import (
    Backend,
    BackendError,
    UpstreamClientError,
    estimate_tokens,
    extract_usage,
)
from .cache import is_cacheable
from .config import Tenant
from .errors import (
    AllBackendsFailed,
    AuthError,
    ForgeError,
    QueueFull,
    QueueWaitTimeout,
    QuotaExceeded,
    UsageUnavailable,
)

router = APIRouter()

# The event loop only weakly references tasks: without a strong reference
# a fire-and-forget breaker update or quota charge can be garbage-collected
# before it runs (see asyncio.create_task docs).
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def authenticate(request: Request) -> Tenant:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        key = auth[7:].strip()
    else:
        key = request.headers.get("x-api-key", "")
    tenant = request.app.state.tenants.get(key)
    if tenant is None:
        raise AuthError("invalid or missing API key")
    return tenant


def error_response(err: ForgeError, retry_after: int | None = None) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse(
        {"error": {"message": err.message, "type": err.error_type}},
        status_code=err.status_code,
        headers=headers,
    )


def _sync_breaker_gauge(state) -> None:
    metrics.BREAKER_STATE.set(int(state.breaker.state))


async def _call_with_failover(state, call):
    """Run `call(backend)` on the primary (if the breaker allows), else or
    on failure the fallback; return (backend, result) from the first that
    succeeds, keeping the breaker and its gauge in step."""
    order = [state.fallback]
    if await state.breaker.allow_primary():
        order.insert(0, state.primary)
    last_err: BackendError | None = None
    for backend in order:
        try:
            result = await call(backend)
        except BackendError as err:
            if backend is state.primary:
                await state.breaker.record_failure()
                metrics.FAILOVERS.labels(state.primary.name, state.fallback.name).inc()
            last_err = err
            continue
        except UpstreamClientError:
            # Upstream is alive; the 4xx is the caller's fault. Must be
            # recorded as breaker success — a half-open probe that ends
            # here would otherwise leave the breaker wedged in HALF_OPEN
            # (probe never resolved) and permanently disable the primary.
            if backend is state.primary:
                await state.breaker.record_success()
            _sync_breaker_gauge(state)
            raise
        if backend is state.primary:
            await state.breaker.record_success()
        _sync_breaker_gauge(state)
        return backend, result
    _sync_breaker_gauge(state)
    raise AllBackendsFailed(str(last_err) if last_err else "no backend available")


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/v1/models")
async def models(request: Request):
    alias = request.app.state.settings.model_alias
    return {
        "object": "list",
        "data": [{"id": alias, "object": "model", "owned_by": "forge"}],
    }


@router.get("/v1/usage")
async def usage(request: Request):
    try:
        tenant = authenticate(request)
    except AuthError as err:
        return error_response(err)
    try:
        return await request.app.state.quotas.usage(tenant)
    except UsageUnavailable as err:
        return error_response(err, retry_after=5)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    state = request.app.state
    started = time.monotonic()
    try:
        tenant = authenticate(request)
    except AuthError as err:
        metrics.REQUESTS.labels("unknown", "none", "auth_error").inc()
        return error_response(err)

    try:
        payload = await request.json()
        assert isinstance(payload, dict) and payload.get("messages")
    except Exception:
        return JSONResponse(
            {"error": {"message": "body must be JSON with a 'messages' array",
                       "type": "invalid_request"}},
            status_code=400,
        )

    with state.tracer.start_as_current_span("gateway.chat_completions") as span:
        span.set_attribute("forge.tenant", tenant.name)
        span.set_attribute("forge.stream", bool(payload.get("stream")))

        try:
            await state.quotas.check(tenant)
        except QuotaExceeded as err:
            metrics.SHED.labels(reason="quota").inc()
            metrics.REQUESTS.labels(tenant.name, "none", "quota_exhausted").inc()
            span.set_attribute("forge.outcome", "quota_exhausted")
            return error_response(err, retry_after=3600)

        cached = await state.cache.get(tenant.name, payload)
        if cached is not None:
            metrics.CACHE_EVENTS.labels(result="hit").inc()
            metrics.REQUESTS.labels(tenant.name, "cache", "ok").inc()
            span.set_attribute("forge.backend", "cache")
            return JSONResponse(
                cached, headers={"x-forge-backend": "cache", "x-forge-cache": "hit"}
            )
        if is_cacheable(payload):
            metrics.CACHE_EVENTS.labels(result="miss").inc()

        try:
            if payload.get("stream"):
                return await _handle_stream(state, tenant, payload, span, started)
            async with state.admission.slot(tenant.name, tenant.weight):
                return await _handle_unary(state, tenant, payload, span, started)
        except (QueueFull, QueueWaitTimeout) as err:
            metrics.SHED.labels(reason=err.error_type).inc()
            metrics.REQUESTS.labels(tenant.name, "none", err.error_type).inc()
            span.set_attribute("forge.outcome", err.error_type)
            return error_response(err, retry_after=1)
        except UpstreamClientError as err:
            metrics.REQUESTS.labels(tenant.name, "upstream", "client_error").inc()
            try:
                body = json.loads(err.body)
            except json.JSONDecodeError:
                body = {"error": {"message": err.body, "type": "upstream_error"}}
            return JSONResponse(body, status_code=err.status_code)
        except AllBackendsFailed as err:
            metrics.REQUESTS.labels(tenant.name, "none", "all_backends_failed").inc()
            span.set_attribute("forge.outcome", "all_backends_failed")
            return error_response(err)
        except Exception:
            # Anything unexpected is still a client-visible 500: count it so
            # the error-rate SLO alert sees it.
            metrics.REQUESTS.labels(tenant.name, "none", "error").inc()
            span.set_attribute("forge.outcome", "error")
            raise


async def _handle_unary(state, tenant, payload, span, started) -> JSONResponse:
    backend, response = await _call_with_failover(state, lambda b: b.chat(payload))

    prompt_toks, completion_toks = extract_usage(response)
    if prompt_toks + completion_toks == 0:
        prompt_toks = estimate_tokens(json.dumps(payload.get("messages", [])))
        completion_toks = estimate_tokens(
            "".join(
                (c.get("message") or {}).get("content") or ""
                for c in response.get("choices", [])
            )
        )
    await state.quotas.consume(tenant, prompt_toks + completion_toks)
    metrics.TOKENS.labels(tenant.name, "prompt").inc(prompt_toks)
    metrics.TOKENS.labels(tenant.name, "completion").inc(completion_toks)
    metrics.LATENCY.labels(backend.name).observe(time.monotonic() - started)
    metrics.REQUESTS.labels(tenant.name, backend.name, "ok").inc()
    if await state.cache.put(tenant.name, payload, response):
        metrics.CACHE_EVENTS.labels(result="store").inc()
    span.set_attribute("forge.backend", backend.name)
    span.set_attribute("forge.tokens.completion", completion_toks)
    return JSONResponse(
        response,
        headers={"x-forge-backend": backend.name, "x-forge-cache": "miss"},
    )


async def _handle_stream(state, tenant, payload, span, started) -> StreamingResponse:
    await state.admission.acquire(tenant.name, tenant.weight)
    try:
        backend_used, handle = await _call_with_failover(
            state, lambda b: b.start_stream(payload)
        )
    except BaseException:
        state.admission.release()
        raise

    span.set_attribute("forge.backend", backend_used.name)
    return StreamingResponse(
        _forward_stream(state, tenant, payload, handle, backend_used, started),
        media_type="text/event-stream",
        headers={"x-forge-backend": backend_used.name, "x-forge-cache": "bypass"},
    )


async def _forward_stream(
    state, tenant, payload, handle, backend: Backend, started
):
    """Forward SSE lines, watching them for the usage chunk so streamed
    requests are metered. Cleanup happens in `finally` without awaiting
    (a client disconnect delivers GeneratorExit, where awaits are unsafe);
    async cleanup is spawned as a task instead."""
    first_token_seen = False
    prompt_toks = 0
    completion_toks = 0
    completion_chars = 0
    outcome = "ok"
    try:
        async for line in handle.aiter_lines():
            if not first_token_seen and line.startswith("data:"):
                metrics.TTFT.labels(backend.name).observe(time.monotonic() - started)
                first_token_seen = True
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    try:
                        chunk = json.loads(data)
                        usage = chunk.get("usage")
                        if usage:
                            prompt_toks = int(usage.get("prompt_tokens", 0))
                            completion_toks = int(usage.get("completion_tokens", 0))
                        for choice in chunk.get("choices", []):
                            delta = (choice.get("delta") or {}).get("content") or ""
                            completion_chars += len(delta)
                    except json.JSONDecodeError:
                        pass
            yield line + "\n"
    except Exception:
        outcome = "stream_error"
        if backend is state.primary:
            # can't await in this except path safely on disconnects either;
            # schedule the breaker update.
            _spawn(state.breaker.record_failure())
    finally:
        state.admission.release()
        if prompt_toks + completion_toks == 0 and completion_chars:
            # No usage chunk from upstream: estimate both sides, as unary does.
            prompt_toks = estimate_tokens(json.dumps(payload.get("messages", [])))
            completion_toks = max(completion_chars // 4, 1)
        metrics.TOKENS.labels(tenant.name, "prompt").inc(prompt_toks)
        metrics.TOKENS.labels(tenant.name, "completion").inc(completion_toks)
        metrics.LATENCY.labels(backend.name).observe(time.monotonic() - started)
        metrics.REQUESTS.labels(tenant.name, backend.name, outcome).inc()
        _spawn(_finalize_stream(state, tenant, handle, prompt_toks + completion_toks))


async def _finalize_stream(state, tenant, handle, total_tokens: int):
    try:
        await handle.aclose()
    finally:
        await state.quotas.consume(tenant, total_tokens)
