"""Home feed, fan-out on write (FEED_MODE=write), with a hybrid for big accounts.

Fan-out on read (the default) builds the feed per request: "latest posts by everyone I
follow" straight from Postgres. Fan-out on write does that work when a post is created
instead: the post is pushed into a Redis sorted set per follower, `feed:{user_id}`, so
reading a feed is a range read on one key plus one lookup of the posts themselves.

  write   new post -> ZADD into the author's and every follower's feed (batched Lua,
          one call per 500 followers), each feed capped at FEED_LEN.
  read    newest entries below the cursor. A missing feed (new user, evicted or
          expired) is rebuilt from Postgres first.
  follow  backfill the followee's recent posts; unfollow removes them.

Big accounts: an author with more than FANOUT_MAX_FOLLOWERS followers would cost that
many writes per post, so their posts are not fanned out. They go into a `feed:celebs`
set instead, and readers merge in recent posts from the celebrities they follow at read
time (one indexed query). Once marked, an account stays a celebrity, so none of its
posts fall between the two paths.

Order matches the Postgres feed exactly, (created_at, id) newest first: the score is
created_at in whole microseconds (exact in a double until the year 2255), and members
are zero-padded ids, so Redis breaks score ties by id. Each feed holds a sentinel member
"0" (score 0) marking it as built, so an empty feed isn't rebuilt on every read. Feeds
are a cache with a TTL; Postgres stays the source of truth.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
import redis.asyncio as aioredis

FEED_LEN = 500  # newest posts kept per feed; older pages come from Postgres
FEED_TTL = 7 * 24 * 3600  # an idle user's feed expires and is rebuilt on their next visit
BATCH = 500  # feeds updated per Lua call
CELEBS = "feed:celebs"
SENTINEL = "0"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def key(user_id: UUID) -> str:
    return f"feed:{user_id}"


def member(post_id: int) -> str:
    return f"{post_id:020d}"  # same-length strings sort like the numbers


def score(created_at: datetime) -> int:
    return (created_at - EPOCH) // timedelta(microseconds=1)


def entries(rows) -> dict[str, int]:
    return {member(r["id"]): score(r["created_at"]) for r in rows}


# KEYS: feeds   ARGV: score, member, cap
# Only feeds that exist are touched: an inactive user's feed is built when they show up.
PUSH = """
local added = 0
for _, k in ipairs(KEYS) do
  if redis.call('EXISTS', k) == 1 then
    redis.call('ZADD', k, ARGV[1], ARGV[2])
    redis.call('ZREMRANGEBYRANK', k, 1, -(tonumber(ARGV[3]) + 2))  -- rank 0 is the sentinel
    added = added + 1
  end
end
return added
"""

# Posts by everyone `user` follows, plus their own: the fan-out-on-read query.
FOLLOWED_POSTS = """
SELECT p.id, p.created_at FROM posts p
WHERE (p.author_id = $1 OR p.author_id IN (SELECT followee_id FROM follows WHERE follower_id = $1))
  AND ($2::bigint IS NULL OR p.id > $2)
ORDER BY p.created_at DESC, p.id DESC
LIMIT $3
"""


async def follower_ids(pool: asyncpg.Pool, author: UUID) -> list[UUID]:
    rows = await pool.fetch("SELECT follower_id FROM follows WHERE followee_id = $1", author)
    return [r["follower_id"] for r in rows]


async def on_post(r: aioredis.Redis, pool: asyncpg.Pool, post_id: int, author: UUID, max_followers: int) -> int:
    """Fan a new post out. Returns how many existing feeds received it."""
    created_at = await pool.fetchval("SELECT created_at FROM posts WHERE id = $1", post_id)
    targets = [author]
    # Count first: a celebrity's follower list is exactly what we don't want to load.
    count = await pool.fetchval("SELECT count(*) FROM follows WHERE followee_id = $1", author)
    if count > max_followers or await r.sismember(CELEBS, str(author)):
        await r.sadd(CELEBS, str(author))  # readers merge this author's posts in
    else:
        targets += await follower_ids(pool, author)
    added = 0
    for i in range(0, len(targets), BATCH):
        batch = [key(t) for t in targets[i : i + BATCH]]
        added += await r.eval(PUSH, len(batch), *batch, score(created_at), member(post_id), FEED_LEN)
    return added


async def rebuild(r: aioredis.Redis, pool: asyncpg.Pool, user_id: UUID) -> None:
    rows = await pool.fetch(FOLLOWED_POSTS, user_id, None, FEED_LEN)
    k = key(user_id)
    pipe = r.pipeline(transaction=True)
    pipe.delete(k)
    pipe.zadd(k, {SENTINEL: 0, **entries(rows)})
    pipe.expire(k, FEED_TTL)
    await pipe.execute()
    # A post created while we were reading Postgres skipped this feed (it didn't exist
    # yet); pick up anything newer than what we loaded.
    newer = await pool.fetch(FOLLOWED_POSTS, user_id, max((row["id"] for row in rows), default=0), FEED_LEN)
    if newer:
        await r.zadd(k, entries(newer))


async def on_follow(r: aioredis.Redis, pool: asyncpg.Pool, follower: UUID, followee: UUID) -> None:
    """Backfill the followee's recent posts (celebrities are merged at read time anyway)."""
    k = key(follower)
    if not await r.exists(k) or await r.sismember(CELEBS, str(followee)):
        return
    rows = await pool.fetch(
        "SELECT id, created_at FROM posts WHERE author_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2",
        followee,
        FEED_LEN,
    )
    if rows:
        await r.zadd(k, entries(rows))
        await r.zremrangebyrank(k, 1, -(FEED_LEN + 2))


async def on_unfollow(r: aioredis.Redis, pool: asyncpg.Pool, follower: UUID, followee: UUID) -> None:
    k = key(follower)
    if not await r.exists(k):
        return
    rows = await pool.fetch("SELECT id FROM posts WHERE author_id = $1 ORDER BY id DESC LIMIT 5000", followee)
    for i in range(0, len(rows), BATCH):
        await r.zrem(k, *(member(row["id"]) for row in rows[i : i + BATCH]))


async def page_ids(
    r: aioredis.Redis,
    pool: asyncpg.Pool,
    user_id: UUID,
    before: tuple[datetime, int] | None,
    limit: int,
) -> list[int] | None:
    """Up to limit+1 post ids, newest first, older than the cursor `before`. None means
    the cached window ran out (the feed was trimmed at FEED_LEN), so read Postgres."""
    k = key(user_id)
    if await r.zscore(k, SENTINEL) is None:
        await rebuild(r, pool, user_id)
    top, ties = "+inf", 0
    if before:
        # Inclusive of the cursor's own microsecond, so same-instant posts with a lower
        # id aren't skipped; the ones at or above the cursor are filtered out below.
        top = score(before[0])
        ties = await r.zcount(k, top, top)
    pipe = r.pipeline(transaction=False)
    pipe.zrevrangebyscore(k, top, "(0", start=0, num=limit + 1 + ties, withscores=True)
    pipe.zcard(k)
    pipe.smembers(CELEBS)
    pipe.expire(k, FEED_TTL)
    cached, size, celebs, _ = await pipe.execute()
    found = [(int(s), int(m)) for m, s in cached]
    if before:
        found = [(s, i) for s, i in found if (s, i) < (top, before[1])]

    if celebs:  # merge in recent posts from big accounts this user follows
        rows = await pool.fetch(
            """
            SELECT p.id, p.created_at FROM posts p
            JOIN follows f ON f.followee_id = p.author_id AND f.follower_id = $1
            WHERE p.author_id = ANY($2::uuid[])
              AND ($3::timestamptz IS NULL OR (p.created_at, p.id) < ($3, $4))
            ORDER BY p.created_at DESC, p.id DESC LIMIT $5
            """,
            user_id,
            [UUID(c) for c in celebs],
            before[0] if before else None,
            before[1] if before else None,
            limit + 1,
        )
        found = sorted(set(found) | {(score(row["created_at"]), row["id"]) for row in rows}, reverse=True)

    found = found[: limit + 1]
    if len(found) <= limit and size - 1 >= FEED_LEN:
        return None  # older than the cached window
    return [i for _, i in found]
