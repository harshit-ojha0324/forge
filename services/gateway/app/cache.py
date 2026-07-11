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

import redis.asyncio as aioredis

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
        raw = await self._redis.get(cache_key(payload))
        return json.loads(raw) if raw else None

    async def put(self, payload: dict, response: dict) -> bool:
        if not (self.enabled and is_cacheable(payload)):
            return False
        await self._redis.set(
            cache_key(payload), json.dumps(response), ex=self._ttl_s
        )
        return True
