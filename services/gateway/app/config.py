"""Gateway configuration.

Everything is driven by environment variables so the same image runs
locally (docker-compose) and on GKE (Helm values -> env).
"""
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class Tenant(BaseModel):
    name: str
    api_key: str
    daily_token_quota: int


class Settings(BaseSettings):
    # Backends (both speak the OpenAI chat-completions protocol).
    primary_base_url: str = "http://mock-vllm:8000/v1"
    primary_api_key: str = "not-needed"
    primary_model: str = "qwen2.5-3b-instruct"
    fallback_base_url: str = "http://mock-gemini:8000/v1"
    fallback_api_key: str = "not-needed"
    fallback_model: str = "gemini-2.0-flash"

    # Public alias clients use in the `model` field.
    model_alias: str = "forge-default"

    # Admission control.
    max_concurrency: int = 8
    queue_max_waiting: int = 32
    queue_wait_timeout_s: float = 10.0

    # Circuit breaker.
    breaker_failure_threshold: int = 3
    breaker_cooldown_s: float = 15.0

    # Upstream call behaviour.
    request_timeout_s: float = 60.0
    connect_timeout_s: float = 2.0

    # Response cache.
    cache_enabled: bool = True
    cache_ttl_s: int = 300

    redis_url: str = "redis://redis:6379/0"
    tenants_file: str = "/etc/forge/tenants.yaml"

    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "forge-gateway"

    model_config = {"env_prefix": "FORGE_", "protected_namespaces": ()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_tenants(path: str) -> dict[str, Tenant]:
    """Load tenants keyed by API key for O(1) auth lookup."""
    raw = yaml.safe_load(Path(path).read_text())
    tenants = [Tenant(**t) for t in raw["tenants"]]
    return {t.api_key: t for t in tenants}
