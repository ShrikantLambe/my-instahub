"""Per-user rate limits on writes (comments, likes, follows, uploads, ...).

A fixed window per user and action: one Redis EVAL per request (INCR, plus EXPIRE on the
first hit), so instances share the count. Without Redis the counts are kept in this
process, which is right for a single instance. If Redis is unreachable we let the
request through: a hiccup in the limiter must not take writes down with it.
"""

import logging
import time
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request

from app.auth import current_user_id
from app.config import settings

log = logging.getLogger(__name__)

HIT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""

_local: dict[str, tuple[int, float]] = {}  # key -> (count, window end)


async def hit(r: aioredis.Redis | None, key: str, window: int) -> int:
    """Count one request in the current window; returns the count so far."""
    if r is not None:
        return int(await r.eval(HIT, 1, key, window))
    now = time.monotonic()
    count, ends = _local.get(key, (0, now + window))
    if ends <= now:
        count, ends = 0, now + window
    _local[key] = (count + 1, ends)
    if len(_local) > 10_000:  # forget windows that are over
        for k, (_, e) in list(_local.items()):
            if e <= now:
                del _local[k]
    return count + 1


def limit(action: str, per_window: int, window: int):
    """A dependency: at most `per_window` calls per `window` seconds per user."""

    async def check(request: Request, user_id: UUID = Depends(current_user_id)) -> UUID:
        if not settings.rate_limits:
            return user_id
        try:
            n = await hit(request.app.state.redis, f"rl:{action}:{user_id}", window)
        except Exception:
            log.warning("rate limiter unavailable; allowing the request", exc_info=True)
            return user_id
        if n > per_window:
            raise HTTPException(
                status_code=429,
                detail="You're doing that too often. Try again in a moment.",
                headers={"Retry-After": str(window)},
            )
        return user_id

    return check
