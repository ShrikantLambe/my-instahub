"""Phase 10: home feed, fan-out on read vs fan-out on write.

1. Builds a follow graph (local database only): AUTHORS authors with POSTS posts each,
   and READERS readers who each follow FOLLOWS random authors.
2. For each FEED_MODE, starts the API and has k6 open random readers' feeds at a fixed
   rate (loadtest/feed.js), sampling how busy Postgres is.
3. Times the write side: fanning one post out to 100 ... 10,000 follower feeds, and
   the celebrity path that skips it.

    cd api && uv run python -m scripts.feedtest --k6 /path/to/k6
"""

import argparse
import asyncio
import json
import random
import secrets
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import jwt
import redis.asyncio as aioredis

from app import feed
from app.config import settings
from scripts.loadtest import LOADTEST, k6_summary, refuse_remote, sample_locks, start_api, wait_healthy

NS = uuid.UUID("2b0d7f9e-31c4-4c55-9d1e-5a3f9b0c7e11")


def uid(kind: str, i: int) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}-{i}")


async def build_graph(pool: asyncpg.Pool, authors: int, posts: int, readers: int, follows: int) -> list[uuid.UUID]:
    reader_ids = [uid("reader", i) for i in range(readers)]
    if await pool.fetchval("SELECT count(*) FROM users WHERE username LIKE 'ft\\_r%'") >= readers:
        return reader_ids  # already built
    rng = random.Random(42)
    author_ids = [uid("author", i) for i in range(authors)]
    await pool.executemany(
        "INSERT INTO users (id, username) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        [(a, f"ft_a{i:05d}") for i, a in enumerate(author_ids)]
        + [(r, f"ft_r{i:05d}") for i, r in enumerate(reader_ids)],
    )
    now = datetime.now(timezone.utc)
    rows = [
        (a, f"ft post {j}", now - timedelta(minutes=rng.randrange(60 * 24 * 30)))
        for a in author_ids
        for j in range(posts)
    ]
    rows.sort(key=lambda r: r[2])  # insert oldest first, so ids follow created_at
    async with pool.acquire() as conn:
        await conn.copy_records_to_table("posts", records=[(a, c, "seed/x.jpg", t) for a, c, t in rows],
                                         columns=["author_id", "caption", "image_key", "created_at"])
        await conn.copy_records_to_table(
            "follows",
            records=[(r, a) for r in reader_ids for a in rng.sample(author_ids, follows)],
            columns=["follower_id", "followee_id"],
        )
        await conn.execute("ANALYZE posts; ANALYZE follows;")
    return reader_ids


def write_tokens(readers: list[uuid.UUID], secret: str) -> None:
    issuer = f"{settings.supabase_url}/auth/v1" if settings.supabase_url else None
    exp = int(time.time()) + 3600
    tokens = []
    for r in readers:
        claims = {"sub": str(r), "aud": "authenticated", "exp": exp}
        if issuer:
            claims["iss"] = issuer
        tokens.append(jwt.encode(claims, secret, algorithm="HS256"))
    (LOADTEST / "feed_tokens.json").write_text(json.dumps({"tokens": tokens}))


async def time_cold_reads(api: str, tokens: list[str]) -> float:
    """Every reader opens their feed once, in turn: the first-ever read per user."""
    async with httpx.AsyncClient(base_url=api, timeout=30) as http:
        t0 = time.perf_counter()
        for t in tokens:
            (await http.get("/feed?limit=10", headers={"Authorization": f"Bearer {t}"})).raise_for_status()
        return round((time.perf_counter() - t0) / len(tokens) * 1000, 2)


async def run_mode(args, mode: str, secret: str) -> dict:
    r = aioredis.from_url(args.redis_url, decode_responses=True)
    async for k in r.scan_iter("feed:*"):  # every run starts cold
        await r.delete(k)
    await r.aclose()
    api = f"http://localhost:{args.port}"
    proc = start_api("direct", args.port, args.workers, secret, args.redis_url, {"FEED_MODE": mode})
    try:
        health = await wait_healthy(api)
        assert health.get("feed_mode") == mode, health
        tokens = json.loads((LOADTEST / "feed_tokens.json").read_text())["tokens"]
        cold_ms = await time_cold_reads(api, tokens)
        stop = asyncio.Event()
        sampler = asyncio.create_task(sample_locks(settings.database_url, stop))
        out = LOADTEST / "out"
        out.mkdir(exist_ok=True)
        summary = out / f"feed-{mode}.json"
        k6 = await asyncio.create_subprocess_exec(
            args.k6, "run", "--quiet", "--summary-export", str(summary),
            "-e", f"API={api}", "-e", f"RATE={args.rate}", "-e", f"DURATION={args.duration}",
            str(LOADTEST / "feed.js"), stdout=asyncio.subprocess.DEVNULL,
        )
        await k6.wait()
        stop.set()
        busy = await sampler
        return {"mode": mode, "first_read_ms": cold_ms, **k6_summary(summary),
                "pg_active_queries_avg": busy["active_queries_avg"]}
    finally:
        proc.terminate()
        proc.wait(timeout=15)


async def time_fanout(pool: asyncpg.Pool, redis_url: str) -> list[dict]:
    """Cost of one post on the write side, by follower count (all followers active)."""
    r = aioredis.from_url(redis_url, decode_responses=True)
    results = []
    try:
        fans = [uid("fan", i) for i in range(10_001)]
        await pool.executemany(
            "INSERT INTO users (id, username) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            [(f, f"ft_f{i:05d}") for i, f in enumerate(fans)],
        )
        for n in (100, 1_000, 10_000, 10_001):
            author = uid("star", n)
            await pool.execute("INSERT INTO users (id, username) VALUES ($1, $2) ON CONFLICT DO NOTHING", author, f"ft_s{n}")
            await pool.execute("DELETE FROM follows WHERE followee_id = $1", author)
            await pool.executemany("INSERT INTO follows (follower_id, followee_id) VALUES ($1, $2)", [(f, author) for f in fans[:n]])
            await r.srem(feed.CELEBS, str(author))
            pipe = r.pipeline(transaction=False)
            for f in fans[:n]:  # every follower is active: their feed exists
                pipe.zadd(feed.key(f), {feed.SENTINEL: 0})
            await pipe.execute()
            post_id = await pool.fetchval(
                "INSERT INTO posts (author_id, image_key) VALUES ($1, 'seed/x.jpg') RETURNING id", author
            )
            t0 = time.perf_counter()
            added = await feed.on_post(r, pool, post_id, author, 10_000)
            ms = (time.perf_counter() - t0) * 1000
            results.append({"followers": n, "feeds_written": added, "ms": round(ms, 1),
                            "path": "celebrity (merged at read)" if added <= 1 and n > 10_000 else "fan-out"})
    finally:
        await r.aclose()
    return results


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--authors", type=int, default=1000)
    p.add_argument("--posts", type=int, default=30, help="posts per author")
    p.add_argument("--readers", type=int, default=300)
    p.add_argument("--follows", type=int, default=200, help="authors each reader follows")
    p.add_argument("--rate", type=int, default=300, help="feed opens per second")
    p.add_argument("--duration", default="20s")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--port", type=int, default=8100)
    p.add_argument("--k6", default="k6")
    p.add_argument("--redis-url", default=settings.redis_url or "redis://localhost:6379/0")
    args = p.parse_args()

    refuse_remote(settings.database_url)
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=4, statement_cache_size=0)
    try:
        readers = await build_graph(pool, args.authors, args.posts, args.readers, args.follows)
        # Always a throwaway secret: the API under test gets the same one, and tokens signed
    # with the real secret would be valid against the real deployment.
    secret = secrets.token_urlsafe(32)
        write_tokens(readers, secret)
        results = {"reads": [], "fanout": []}
        for mode in ("read", "write"):
            print(f"--- FEED_MODE={mode}: {args.rate} feed opens/s for {args.duration}", flush=True)
            res = await run_mode(args, mode, secret)
            print(json.dumps(res, indent=2), flush=True)
            results["reads"].append(res)
        print("--- write side: one post fanned out", flush=True)
        results["fanout"] = await time_fanout(pool, args.redis_url)
        print(json.dumps(results["fanout"], indent=2), flush=True)
        (LOADTEST / "out" / "feed-results.json").write_text(json.dumps(results, indent=2))
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
