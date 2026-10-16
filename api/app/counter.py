"""Buffered like counter (COUNTER_MODE=buffered), plus the reconciliation job.

Direct mode increments posts.like_count inside each like's transaction. Every like of
a hot post then waits for the same row lock, so they queue one behind another. Buffered
mode removes that queue:

  like/unlike   INSERT/DELETE the likes row only (FK checks take a shared lock that
                doesn't block other likers), then in Redis, atomically:
                  post:{id}:n        += delta   live count shown to everyone
                  post:{id}:v        += 1       version, so copies never go backwards
                  post:{id}:pending  += delta   not yet written to Postgres
                  likes:dirty        ∪= {id}
  flusher       every FLUSH_INTERVAL: take each dirty post's pending delta and apply
                them all to posts.like_count in one UPDATE.
  reconcile     recount from the likes table (the source of truth) and repair any
                drift, e.g. a delta lost if the process died between taking it from
                Redis and committing it to Postgres.

Starting a live count. The first time a post is touched (or after Redis loses its keys)
the live count starts at posts.like_count + pending: every like is in exactly one of
those, or not bumped yet (then its own bump adds it). Counting likes rows instead would
race: a like committed just before the COUNT and bumped just after would count twice.
The flusher moves pending into posts.like_count, so starting a count and flushing take
a short Redis lock to never interleave. The version starts above the one stored in
Postgres, so it continues where direct mode left off.
"""

import asyncio
import contextlib
import logging
import uuid

import asyncpg
import redis.asyncio as aioredis

from app.likes import LikeCount, PostNotFound

log = logging.getLogger(__name__)

DIRTY = "likes:dirty"


def count_key(post_id: int) -> str:
    return f"post:{post_id}:n"


def version_key(post_id: int) -> str:
    return f"post:{post_id}:v"


def pending_key(post_id: int) -> str:
    return f"post:{post_id}:pending"


# KEYS: count, version, pending, dirty   ARGV: delta, post_id
# Pending/dirty always move (the change must reach Postgres). The live count and version
# only move once started; otherwise return nil and the caller starts the count.
BUMP = """
redis.call('INCRBY', KEYS[3], ARGV[1])
redis.call('SADD', KEYS[4], ARGV[2])
if redis.call('EXISTS', KEYS[1]) == 0 then return nil end
local n = redis.call('INCRBY', KEYS[1], ARGV[1])
local v = redis.call('INCR', KEYS[2])
return {n, v}
"""

# KEYS: count, version, pending   ARGV: flushed count, version.
# Start the live count at flushed + pending; first writer wins; returns what's stored.
INIT = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  local pending = tonumber(redis.call('GET', KEYS[3]) or '0')
  redis.call('SET', KEYS[1], tonumber(ARGV[1]) + pending)
end
redis.call('SET', KEYS[2], ARGV[2], 'NX')
return {tonumber(redis.call('GET', KEYS[1])), tonumber(redis.call('GET', KEYS[2]))}
"""

# KEYS: pending   ARGV: amount just written to Postgres.
SETTLE = """
local left = redis.call('DECRBY', KEYS[1], ARGV[1])
if left == 0 then redis.call('DEL', KEYS[1]) end
return left
"""

LOCK = "likes:flush-lock"
RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


async def _try_lock(r: aioredis.Redis, ttl_ms: int) -> str | None:
    token = uuid.uuid4().hex
    return token if await r.set(LOCK, token, nx=True, px=ttl_ms) else None


@contextlib.asynccontextmanager
async def flush_lock(r: aioredis.Redis, wait: float = 5.0, ttl_ms: int = 10_000):
    """Starting a count and flushing must not interleave (see module docstring)."""
    deadline = asyncio.get_running_loop().time() + wait
    while (token := await _try_lock(r, ttl_ms)) is None:
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("like counter lock busy")
        await asyncio.sleep(0.01)
    try:
        yield
    finally:
        await r.eval(RELEASE, 1, LOCK, token)


async def _read_counts(r: aioredis.Redis, post_ids: list[int]) -> dict[int, LikeCount]:
    raw = await r.mget([k for i in post_ids for k in (count_key(i), version_key(i))])
    return {
        pid: LikeCount(int(raw[2 * idx]), int(raw[2 * idx + 1]))
        for idx, pid in enumerate(post_ids)
        if raw[2 * idx] is not None and raw[2 * idx + 1] is not None
    }


async def _start_counts(
    r: aioredis.Redis, pool: asyncpg.Pool, post_ids: list[int], wait: float = 5.0
) -> dict[int, LikeCount]:
    """Start live counts that are missing from Redis; returns every count it could find.

    A cold post that suddenly gets hundreds of likes sends them all here at once. Only
    one needs the lock: the rest re-read while they wait and leave as soon as the count
    exists (it includes their delta, which BUMP already put in pending).

    The version starts one above Postgres's, so the started count (which may already
    include pending likes) always outranks the copy clients loaded from Postgres.
    """
    out: dict[int, LikeCount] = {}
    missing = list(post_ids)
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        out.update(await _read_counts(r, missing))
        missing = [i for i in missing if i not in out]
        if not missing:
            return out
        token = await _try_lock(r, 10_000)
        if token is not None:
            break
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("like counter lock busy")
        await asyncio.sleep(0.01)
    try:
        rows = await pool.fetch(
            "SELECT id, like_count, like_version FROM posts WHERE id = ANY($1::bigint[])", missing
        )
        for row in rows:
            pid = row["id"]
            n, v = await r.eval(
                INIT, 3, count_key(pid), version_key(pid), pending_key(pid),
                row["like_count"], row["like_version"] + 1,
            )
            out[pid] = LikeCount(int(n), int(v))
    finally:
        await r.eval(RELEASE, 1, LOCK, token)
    return out


async def get_counts(r: aioredis.Redis, pool: asyncpg.Pool, post_ids: list[int]) -> dict[int, LikeCount]:
    """Live counts for many posts: one MGET; posts not yet in Redis get a count started."""
    ids = list(dict.fromkeys(post_ids))
    if not ids:
        return {}
    found = await _read_counts(r, ids)
    missing = [i for i in ids if i not in found]
    if missing:
        found.update(await _start_counts(r, pool, missing))
    return found


async def bump(r: aioredis.Redis, pool: asyncpg.Pool, post_id: int, delta: int) -> LikeCount:
    """Record a like (+1) or unlike (-1) that was just committed to the likes table."""
    keys = (count_key(post_id), version_key(post_id), pending_key(post_id), DIRTY)
    result = await r.eval(BUMP, 4, *keys, delta, post_id)
    if result is not None:
        return LikeCount(int(result[0]), int(result[1]))
    # First touch: our delta is already in pending, so the started count includes it.
    initialized = await _start_counts(r, pool, [post_id])
    if post_id not in initialized:
        raise PostNotFound(post_id)
    return initialized[post_id]


async def current(r: aioredis.Redis, pool: asyncpg.Pool, post_id: int) -> LikeCount:
    counts = await get_counts(r, pool, [post_id])
    if post_id not in counts:
        raise PostNotFound(post_id)
    return counts[post_id]


async def _apply(pool: asyncpg.Pool, rows: list[tuple[int, int, int]]) -> None:
    await pool.execute(
        """
        UPDATE posts p
        SET like_count = p.like_count + d.n, like_version = GREATEST(p.like_version, d.v)
        FROM unnest($1::bigint[], $2::int[], $3::bigint[]) AS d(id, n, v)
        WHERE p.id = d.id
        """,
        [x[0] for x in rows],
        [x[1] for x in rows],
        [x[2] for x in rows],
    )


async def flush(r: aioredis.Redis, pool: asyncpg.Pool, batch: int = 500) -> int:
    """Apply pending deltas to Postgres in one UPDATE. Returns how many posts changed.

    Order matters: read pending, commit to Postgres, then subtract what was written. A
    like arriving meanwhile just stays in pending for the next flush. If Postgres fails,
    nothing was subtracted, so nothing is lost; a crash between commit and subtract
    would apply a delta twice, which the reconcile job repairs.
    """
    async with flush_lock(r):
        ids = [int(i) for i in (await r.spop(DIRTY, batch) or [])]
        if not ids:
            return 0
        values = await r.mget([k for i in ids for k in (pending_key(i), version_key(i))])
        rows = [
            (pid, int(values[2 * i] or 0), int(values[2 * i + 1] or 0))
            for i, pid in enumerate(ids)
            if int(values[2 * i] or 0) != 0
        ]
        if not rows:
            return 0
        try:
            await _apply(pool, rows)
        except Exception:
            await r.sadd(DIRTY, *[pid for pid, _, _ in rows])  # retry next time
            raise
        pipe = r.pipeline(transaction=False)
        for pid, n, _ in rows:
            pipe.eval(SETTLE, 1, pending_key(pid), n)
        await pipe.execute()
        return len(rows)


async def reconcile(pool: asyncpg.Pool, r: aioredis.Redis | None) -> list[dict]:
    """Make posts.like_count (and the live Redis count, if any) agree with the likes table.

    In buffered mode posts.like_count deliberately lags by the pending delta, so the
    target there is truth - pending. Returns the posts that were repaired.
    """
    async with flush_lock(r) if r is not None else contextlib.nullcontext():
        return await _reconcile_locked(pool, r)


async def _reconcile_locked(pool: asyncpg.Pool, r: aioredis.Redis | None) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT p.id, p.like_count, count(l.post_id) AS truth
        FROM posts p LEFT JOIN likes l ON l.post_id = p.id
        GROUP BY p.id
        """
    )
    pending: dict[int, int] = {}
    live: dict[int, int | None] = {}
    if r is not None and rows:
        ids = [row["id"] for row in rows]
        raw = await r.mget([k for i in ids for k in (pending_key(i), count_key(i))])
        for idx, pid in enumerate(ids):
            pending[pid] = int(raw[2 * idx] or 0)
            live[pid] = None if raw[2 * idx + 1] is None else int(raw[2 * idx + 1])

    repaired = []
    for row in rows:
        pid, truth = row["id"], row["truth"]
        target = truth - pending.get(pid, 0)
        fixed = {}
        if row["like_count"] != target:
            await pool.execute(
                "UPDATE posts SET like_count = $2, like_version = like_version + 1 WHERE id = $1",
                pid,
                target,
            )
            fixed["db"] = (row["like_count"], target)
        # Only touch a live count that is quiet (nothing pending), so we don't race a like.
        if live.get(pid) is not None and live[pid] != truth and pending.get(pid, 0) == 0:
            await r.set(count_key(pid), truth)
            await r.incr(version_key(pid))
            fixed["redis"] = (live[pid], truth)
        if fixed:
            repaired.append({"post_id": pid, **fixed})
    if repaired:
        log.warning("reconcile repaired %d post(s): %s", len(repaired), repaired)
    return repaired


async def run_background(r: aioredis.Redis, pool: asyncpg.Pool, flush_every: float, reconcile_every: float):
    """Flusher plus a periodic reconcile, for buffered mode."""
    since_reconcile = 0.0
    while True:
        await asyncio.sleep(flush_every)
        since_reconcile += flush_every
        try:
            while await flush(r, pool) > 0:  # drain a backlog in a few quick rounds
                pass
            if since_reconcile >= reconcile_every:
                since_reconcile = 0.0
                await reconcile(pool, r)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("like counter flush/reconcile failed; will retry", exc_info=True)
