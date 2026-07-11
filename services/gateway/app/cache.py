"""Redis response cache.

Only deterministic, non-streaming requests are cached (temperature == 0,
stream == false): with sampling enabled, identical prompts legitimately
produce different completions, and replaying one would silently change
model behaviour. The key hashes the full request shape so any change in
messages, model, or sampling params is a different entry.

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

CACHEABLE_KEYS = ("model", "messages", "temperature", "top_p", "max_tokens", "n", "stop")


def is_cacheable(payload: dict) -> bool:
    return not payload.get("stream", False) and payload.get("temperature", 1.0) == 0


def cache_key(payload: dict) -> str:
    material = {k: payload.get(k) for k in CACHEABLE_KEYS}
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"forge:cache:{digest}"


class ResponseCache:
    def __init__(self, redis: aioredis.Redis, ttl_s: int, enabled: bool = True):
        self._redis = redis
        self._ttl_s = ttl_s
        self.enabled = enabled

    async def get(self, payload: dict) -> dict | None:
        if not (self.enabled and is_cacheable(payload)):
            return None
        try:
            raw = await self._redis.get(cache_key(payload))
        except (RedisError, OSError) as exc:
            # A dead cache is a slow day, not an outage: fail open.
            REDIS_ERRORS.labels(op="cache_get").inc()
            log.warning("redis unavailable during cache get (%r)", exc)
            return None
        return json.loads(raw) if raw else None

    async def put(self, payload: dict, response: dict) -> bool:
        if not (self.enabled and is_cacheable(payload)):
            return False
        try:
            await self._redis.set(
                cache_key(payload), json.dumps(response), ex=self._ttl_s
            )
        except (RedisError, OSError) as exc:
            REDIS_ERRORS.labels(op="cache_put").inc()
            log.warning("redis unavailable during cache put (%r)", exc)
            return False
        return True
