import httpx
import redis.asyncio as aioredis
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import metrics
from .admission import AdmissionController
from .backends import Backend
from .breaker import CircuitBreaker
from .cache import ResponseCache
from .config import Settings, get_settings, load_tenants
from .quotas import QuotaManager
from .routes import router
from .tracing import setup_tracing


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.tenants = load_tenants(settings.tenants_file)
        app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        app.state.http = httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.request_timeout_s, connect=settings.connect_timeout_s
            ),
            limits=httpx.Limits(max_connections=settings.max_concurrency * 2),
        )
        app.state.primary = Backend(
            "vllm",
            settings.primary_base_url,
            settings.primary_api_key,
            settings.primary_model,
            app.state.http,
        )
        app.state.fallback = Backend(
            "gemini",
            settings.fallback_base_url,
            settings.fallback_api_key,
            settings.fallback_model,
            app.state.http,
        )
        app.state.quotas = QuotaManager(app.state.redis)
        app.state.cache = ResponseCache(
            app.state.redis, settings.cache_ttl_s, settings.cache_enabled
        )
        app.state.admission = AdmissionController(
            settings.max_concurrency,
            settings.queue_max_waiting,
            settings.queue_wait_timeout_s,
        )
        app.state.breaker = CircuitBreaker(
            settings.breaker_failure_threshold, settings.breaker_cooldown_s
        )
        app.state.tracer = setup_tracing(
            settings.otel_service_name, settings.otel_exporter_otlp_endpoint
        )
        metrics.QUEUE_WAITING.set_function(lambda: app.state.admission.waiting)
        metrics.IN_FLIGHT.set_function(lambda: app.state.admission.in_flight)
        yield
        await app.state.http.aclose()
        await app.state.redis.aclose()

    app = FastAPI(title="Forge Gateway", version="1.0.0", lifespan=lifespan)
    app.include_router(router)

    @app.get("/metrics")
    def metrics_endpoint() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
