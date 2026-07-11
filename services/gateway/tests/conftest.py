import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

from app import main as main_module
from app.config import Settings
from app.main import create_app

TENANTS_YAML = """
tenants:
  - name: alpha
    api_key: test-key-alpha
    daily_token_quota: 100000
  - name: beta
    api_key: test-key-beta
    daily_token_quota: 50
"""


@pytest.fixture
def tenants_file(tmp_path):
    path = tmp_path / "tenants.yaml"
    path.write_text(TENANTS_YAML)
    return str(path)


@pytest.fixture
def settings(tenants_file):
    return Settings(
        tenants_file=tenants_file,
        primary_base_url="http://primary.test/v1",
        fallback_base_url="http://fallback.test/v1",
        primary_model="test-primary-model",
        fallback_model="test-fallback-model",
        breaker_failure_threshold=2,
        breaker_cooldown_s=0.2,
        max_concurrency=2,
        queue_max_waiting=2,
        queue_wait_timeout_s=1.0,
        request_timeout_s=5.0,
        cache_ttl_s=60,
    )


@pytest_asyncio.fixture
async def client(settings, monkeypatch):
    monkeypatch.setattr(
        main_module.aioredis,
        "from_url",
        lambda *args, **kwargs: fakeredis.aioredis.FakeRedis(decode_responses=True),
    )
    app = create_app(settings)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gateway.test"
        ) as c:
            c.forge_app = app
            yield c


def completion_body(content="hello from model", prompt_tokens=10, completion_tokens=20):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "test",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def chat_request(stream=False, temperature=1.0, content="hi"):
    body = {
        "model": "forge-default",
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
    }
    if stream:
        body["stream"] = True
    return body


AUTH = {"Authorization": "Bearer test-key-alpha"}
AUTH_BETA = {"Authorization": "Bearer test-key-beta"}
