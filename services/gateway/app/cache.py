"""Redis response cache.

Only deterministic, non-streaming requests are cached (temperature == 0,
stream == false): with sampling enabled, identical prompts legitimately
produce different completions, and replaying one would silently change
model behaviour. The key hashes the whole request minus a few fields that
cannot change the output, so any parameter (tools, response_format, seed,
...) is a different entry: an unknown field costs a miss, never a wrong
answer. Entries are per tenant: a shared hit would tell one tenant what
another one asked.

Upgrading this to a semantic cache (embed the prompt, ANN-search for a
near-duplicate) only requires replacing `cache_key` — the interface is
deliberately key/value.
"""
import hashlib
import json
import logging

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from .metrics import REDIS_ERRORS

log = logging.getLogger("forge.cache")

# Request fields that never change the completion, so never split the cache.
IGNORED_KEYS = frozenset({"stream", "stream_options", "user", "metadata"})


def is_cacheable(payload: dict) -> bool:
    return not payload.get("stream", False) and payload.get("temperature", 1.0) == 0


def cache_key(tenant: str, payload: dict) -> str:
    material = {k: v for k, v in payload.items() if k not in IGNORED_KEYS}
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"forge:cache:{tenant}:{digest}"


class ResponseCache:
    def __init__(self, redis: aioredis.Redis, ttl_s: int, enabled: bool = True):
        self._redis = redis
        self._ttl_s = ttl_s
        self.enabled = enabled

    async def get(self, tenant: str, payload: dict) -> dict | None:
        if not (self.enabled and is_cacheable(payload)):
            return None
        try:
            raw = await self._redis.get(cache_key(tenant, payload))
        except (RedisError, OSError) as exc:
            # A dead cache is a slow day, not an outage: fail open.
            REDIS_ERRORS.labels(op="cache_get").inc()
            log.warning("redis unavailable during cache get (%r)", exc)
            return None
        return json.loads(raw) if raw else None

    async def put(self, tenant: str, payload: dict, response: dict) -> bool:
        if not (self.enabled and is_cacheable(payload)):
            return False
        try:
            await self._redis.set(
                cache_key(tenant, payload), json.dumps(response), ex=self._ttl_s
            )
        except (RedisError, OSError) as exc:
            REDIS_ERRORS.labels(op="cache_put").inc()
            log.warning("redis unavailable during cache put (%r)", exc)
            return False
        return True
