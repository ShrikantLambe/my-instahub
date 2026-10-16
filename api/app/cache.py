"""Like counts in Redis, cache-aside, with versions so the cache never goes backwards.

Readers ask Redis first and fall back to Postgres on a miss, then fill the cache with
a TTL. Writers update Postgres first (the source of truth), then write the new count
here. Two likes can finish their Postgres transactions in one order and reach Redis
in the other; a plain SET would then leave the older count cached. So every value is
stored as "version:count" and a tiny Lua script only replaces it with a newer version.

Redis is optional: with no REDIS_URL every read simply goes to Postgres.
"""

import logging

import asyncpg
import redis.asyncio as aioredis

from app.likes import LikeCount

log = logging.getLogger(__name__)

TTL_SECONDS = 300

# KEYS[1] = key; ARGV = version, count, ttl. Writes only if ARGV version is newer.
SET_IF_NEWER = """
local cur = redis.call('GET', KEYS[1])
if cur then
  local v = tonumber(string.match(cur, '^(%d+):'))
  if v and v >= tonumber(ARGV[1]) then return 0 end
end
redis.call('SET', KEYS[1], ARGV[1] .. ':' .. ARGV[2], 'EX', ARGV[3])
return 1
"""


def key(post_id: int) -> str:
    return f"post:{post_id}:likes"


def _parse(raw: str | None) -> LikeCount | None:
    if not raw:
        return None
    version, _, count = raw.partition(":")
    return LikeCount(int(count), int(version))


async def store(r: aioredis.Redis | None, post_id: int, value: LikeCount) -> bool:
    """Write a count if it's newer than what's cached. Cache errors never fail a request."""
    if r is None:
        return False
    try:
        return bool(await r.eval(SET_IF_NEWER, 1, key(post_id), value.version, value.count, TTL_SECONDS))
    except Exception:  # Postgres already has the truth; a cache hiccup must not fail the like
        log.warning("like cache write failed for post %s", post_id, exc_info=True)
        return False


async def get_counts(
    r: aioredis.Redis | None, pool: asyncpg.Pool, post_ids: list[int]
) -> dict[int, LikeCount]:
    """Cache-aside read for many posts: one MGET, then one Postgres query for the misses."""
    ids = list(dict.fromkeys(post_ids))
    found: dict[int, LikeCount] = {}
    if r is not None and ids:
        try:
            for pid, raw in zip(ids, await r.mget([key(i) for i in ids])):
                if (hit := _parse(raw)) is not None:
                    found[pid] = hit
        except Exception:
            log.warning("like cache read failed; falling back to Postgres", exc_info=True)
    misses = [i for i in ids if i not in found]
    if misses:
        rows = await pool.fetch(
            "SELECT id, like_count, like_version FROM posts WHERE id = ANY($1::bigint[])", misses
        )
        for row in rows:
            found[row["id"]] = LikeCount(row["like_count"], row["like_version"])
        await store_many(r, {row["id"]: found[row["id"]] for row in rows})
    return found


async def store_many(r: aioredis.Redis | None, values: dict[int, LikeCount]) -> None:
    """Refill several counts in one round trip (one pipeline, not one call per post)."""
    if r is None or not values:
        return
    try:
        pipe = r.pipeline(transaction=False)
        for post_id, value in values.items():
            pipe.eval(SET_IF_NEWER, 1, key(post_id), value.version, value.count, TTL_SECONDS)
        await pipe.execute()
    except Exception:
        log.warning("like cache refill failed", exc_info=True)
