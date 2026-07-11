"""Per-tenant daily token quotas backed by Redis.

Usage is tracked per UTC day in `forge:usage:{tenant}:{YYYYMMDD}`.
Check happens at admission; consumption is recorded after the response
(when the real token count is known). A burst of concurrent requests can
therefore overshoot the quota by up to `max_concurrency` requests —
accepted and documented, because pre-reserving tokens for a response of
unknown length would either reject work needlessly or need a
reconciliation pass anyway.
"""
import datetime as dt

import redis.asyncio as aioredis

from .config import Tenant
from .errors import QuotaExceeded

USAGE_KEY_TTL_S = 3 * 24 * 3600  # keep a few days for the spend dashboard


def _usage_key(tenant: str, day: dt.date | None = None) -> str:
    day = day or dt.datetime.now(dt.timezone.utc).date()
    return f"forge:usage:{tenant}:{day.strftime('%Y%m%d')}"


class QuotaManager:
    def __init__(self, redis: aioredis.Redis):
        self._redis = redis

    async def check(self, tenant: Tenant) -> int:
        """Raise QuotaExceeded if the tenant is out of tokens; return remaining."""
        used = int(await self._redis.get(_usage_key(tenant.name)) or 0)
        remaining = tenant.daily_token_quota - used
        if remaining <= 0:
            raise QuotaExceeded(
                f"tenant '{tenant.name}' exhausted daily quota of "
                f"{tenant.daily_token_quota} tokens"
            )
        return remaining

    async def consume(self, tenant: Tenant, tokens: int) -> None:
        if tokens <= 0:
            return
        key = _usage_key(tenant.name)
        pipe = self._redis.pipeline()
        pipe.incrby(key, tokens)
        pipe.expire(key, USAGE_KEY_TTL_S)
        await pipe.execute()

    async def usage(self, tenant: Tenant) -> dict:
        used = int(await self._redis.get(_usage_key(tenant.name)) or 0)
        return {
            "tenant": tenant.name,
            "used_tokens": used,
            "daily_token_quota": tenant.daily_token_quota,
            "remaining_tokens": max(tenant.daily_token_quota - used, 0),
        }
