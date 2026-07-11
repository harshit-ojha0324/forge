"""Per-tenant daily token quotas backed by Redis.

Usage is tracked per UTC day in `forge:usage:{tenant}:{YYYYMMDD}`.
Check happens at admission; consumption is recorded after the response
(when the real token count is known). A burst of concurrent requests can
therefore overshoot the quota by up to `max_concurrency` requests —
accepted and documented, because pre-reserving tokens for a response of
unknown length would either reject work needlessly or need a
reconciliation pass anyway.

Redis failures FAIL OPEN: metering and quota enforcement are
conveniences, not serving dependencies. A dead Redis means requests go
unmetered for its downtime (visible via forge_redis_errors_total), not
that clients see errors. The opposite trade (fail closed) is defensible
only when quotas are hard billing guarantees.
"""
import datetime as dt
import logging

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from .config import Tenant
from .errors import QuotaExceeded
from .metrics import REDIS_ERRORS

log = logging.getLogger("forge.quotas")

USAGE_KEY_TTL_S = 3 * 24 * 3600  # keep a few days for the spend dashboard


def _usage_key(tenant: str, day: dt.date | None = None) -> str:
    day = day or dt.datetime.now(dt.timezone.utc).date()
    return f"forge:usage:{tenant}:{day.strftime('%Y%m%d')}"


class QuotaManager:
    def __init__(self, redis: aioredis.Redis):
        self._redis = redis

    async def check(self, tenant: Tenant) -> int:
        """Raise QuotaExceeded if the tenant is out of tokens; return remaining."""
        try:
            used = int(await self._redis.get(_usage_key(tenant.name)) or 0)
        except (RedisError, OSError) as exc:
            REDIS_ERRORS.labels(op="quota_check").inc()
            log.warning("redis unavailable during quota check (%r); failing open", exc)
            return tenant.daily_token_quota
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
        try:
            key = _usage_key(tenant.name)
            pipe = self._redis.pipeline()
            pipe.incrby(key, tokens)
            pipe.expire(key, USAGE_KEY_TTL_S)
            await pipe.execute()
        except (RedisError, OSError) as exc:
            REDIS_ERRORS.labels(op="quota_consume").inc()
            log.warning("redis unavailable recording usage (%r); dropped %d tokens",
                        exc, tokens)

    async def usage(self, tenant: Tenant) -> dict:
        used = int(await self._redis.get(_usage_key(tenant.name)) or 0)
        return {
            "tenant": tenant.name,
            "used_tokens": used,
            "daily_token_quota": tenant.daily_token_quota,
            "remaining_tokens": max(tenant.daily_token_quota - used, 0),
        }
