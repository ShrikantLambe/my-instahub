"""Phase 6: load-test one hot post in direct and buffered counter mode and compare.

For each mode it:
  1. creates load-test users and one hot post (likes cleared), and writes
     loadtest/tokens.json with a signed token per user;
  2. starts the API (several uvicorn workers) with COUNTER_MODE set;
  3. runs loadtest/hot_post.js with k6 while sampling Postgres for sessions stuck
     waiting on a lock;
  4. waits for the counts to settle and checks API count == posts.like_count ==
     COUNT(likes).

Local databases only: it writes users and tokens, so it refuses any non-local
DATABASE_URL.

    cd api && uv run python -m scripts.loadtest --rate 400 --duration 20s
"""

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import httpx
import jwt
import redis.asyncio as aioredis

from app import cache, counter
from app.config import settings

ROOT = Path(__file__).resolve().parents[2]
LOADTEST = ROOT / "loadtest"
NS = uuid.UUID("6f3c0c1e-6b0e-4b8e-9a52-1c0b7c5e1a06")  # stable ids for load-test users
CAPTION = "Load test: hot post"


def refuse_remote(dsn: str) -> None:
    host = urlsplit(dsn).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1"):
        sys.exit(f"refusing to load-test a non-local database ({host}); this creates users")


async def setup(pool: asyncpg.Pool, r: aioredis.Redis, users: int, secret: str) -> int:
    """Create users and the hot post, clear its likes, write tokens. Returns the post id."""
    ids = [uuid.uuid5(NS, str(i)) for i in range(users)]
    await pool.executemany(
        "INSERT INTO users (id, username, display_name) VALUES ($1, $2, $3) ON CONFLICT (id) DO NOTHING",
        [(uid, f"lt_{i:05d}", f"Load tester {i}") for i, uid in enumerate(ids)],
    )
    author = ids[0]
    post_id = await pool.fetchval(
        "SELECT id FROM posts WHERE author_id = $1 AND caption = $2", author, CAPTION
    ) or await pool.fetchval(
        "INSERT INTO posts (author_id, caption, image_key) VALUES ($1, $2, 'seed/loadtest.jpg') RETURNING id",
        author,
        CAPTION,
    )
    await pool.execute("DELETE FROM likes WHERE post_id = $1", post_id)
    await pool.execute(
        "UPDATE posts SET like_count = 0, like_version = like_version + 1 WHERE id = $1", post_id
    )
    await r.delete(counter.count_key(post_id), counter.version_key(post_id), counter.pending_key(post_id))
    await r.srem(counter.DIRTY, post_id)
    await r.delete(cache.key(post_id))  # direct mode's cache-aside entry

    issuer = f"{settings.supabase_url}/auth/v1" if settings.supabase_url else None
    exp = int(time.time()) + 3600
    tokens = []
    for uid in ids:
        claims = {"sub": str(uid), "aud": "authenticated", "role": "authenticated", "exp": exp}
        if issuer:
            claims["iss"] = issuer
        tokens.append(jwt.encode(claims, secret, algorithm="HS256"))
    (LOADTEST / "tokens.json").write_text(json.dumps({"post_id": post_id, "tokens": tokens}))
    return post_id


async def sample_locks(dsn: str, stop: asyncio.Event, every: float = 0.05) -> dict:
    """Poll pg_stat_activity: how many sessions are waiting on a lock right now."""
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    waiting, active = [], []
    try:
        while not stop.is_set():
            row = await conn.fetchrow(
                """
                SELECT count(*) FILTER (WHERE wait_event_type = 'Lock') AS waiting,
                       count(*) FILTER (WHERE state = 'active') AS active
                FROM pg_stat_activity
                WHERE datname = current_database() AND pid <> pg_backend_pid()
                """
            )
            waiting.append(row["waiting"])
            active.append(row["active"])
            await asyncio.sleep(every)
    finally:
        await conn.close()
    busy = [w for w in waiting if w > 0]
    return {
        "samples": len(waiting),
        "lock_waiters_max": max(waiting, default=0),
        "lock_waiters_avg": round(sum(waiting) / len(waiting), 2) if waiting else 0,
        "samples_with_lock_waits_pct": round(100 * len(busy) / len(waiting), 1) if waiting else 0,
        "active_queries_avg": round(sum(active) / len(active), 2) if active else 0,
    }


async def posts_updates(pool: asyncpg.Pool) -> int:
    await asyncio.sleep(1.5)  # backends report table stats about once a second
    return await pool.fetchval("SELECT n_tup_upd FROM pg_stat_user_tables WHERE relname = 'posts'")


def start_api(
    mode: str, port: int, workers: int, secret: str, redis_url: str, extra_env: dict | None = None
) -> subprocess.Popen:
    env = {
        **os.environ,
        "COUNTER_MODE": mode,
        "REDIS_URL": redis_url,
        "SUPABASE_JWT_SECRET": secret,
        "DATABASE_URL": settings.database_url,
        **(extra_env or {}),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port),
         "--workers", str(workers), "--log-level", "warning", "--no-access-log"],
        cwd=ROOT / "api",
        env=env,
    )


async def wait_healthy(api: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                res = await client.get(f"{api}/health")
                if res.status_code == 200:
                    return res.json()
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.3)
    raise RuntimeError("API did not become healthy")


def k6_summary(path: Path) -> dict:
    m = json.loads(path.read_text())["metrics"]
    dur = m["http_req_duration"]
    out = {
        "requests": int(m["http_reqs"]["count"]),
        "throughput_rps": round(m["http_reqs"]["rate"], 1),
        "p50_ms": round(dur["med"], 1),
        "p95_ms": round(dur["p(95)"], 1),
        "p99_ms": round(dur["p(99)"], 1),
        "max_ms": round(dur["max"], 1),
        "failed_pct": round(100 * m["http_req_failed"]["value"], 2),
        "dropped_iterations": int(m.get("dropped_iterations", {}).get("count", 0)),
    }
    return out


async def run_mode(args, mode: str, secret: str) -> dict:
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2, statement_cache_size=0)
    r = aioredis.from_url(args.redis_url, decode_responses=True)
    api = f"http://localhost:{args.port}"
    proc = None
    try:
        post_id = await setup(pool, r, args.users, secret)
        proc = start_api(mode, args.port, args.workers, secret, args.redis_url)
        health = await wait_healthy(api)
        assert health.get("counter_mode") == mode, health

        updates_before = await posts_updates(pool)
        stop = asyncio.Event()
        sampler = asyncio.create_task(sample_locks(settings.database_url, stop))
        out_dir = LOADTEST / "out"
        out_dir.mkdir(exist_ok=True)
        summary = out_dir / f"{mode}.json"
        k6 = await asyncio.create_subprocess_exec(
            args.k6, "run", "--quiet", "--summary-export", str(summary),
            "-e", f"API={api}", "-e", f"RATE={args.rate}", "-e", f"DURATION={args.duration}",
            str(LOADTEST / "hot_post.js"),
            stdout=asyncio.subprocess.DEVNULL,
        )
        await k6.wait()
        stop.set()
        locks = await sampler

        # Buffered mode: posts.like_count catches up on the next flush.
        truth = await pool.fetchval("SELECT count(*) FROM likes WHERE post_id = $1", post_id)
        t0 = time.monotonic()
        while await pool.fetchval("SELECT like_count FROM posts WHERE id = $1", post_id) != truth:
            if time.monotonic() - t0 > 20:
                break
            await asyncio.sleep(0.1)
        settle_s = round(time.monotonic() - t0, 2)
        async with httpx.AsyncClient() as client:
            api_count = (await client.get(f"{api}/posts/{post_id}")).json()["like_count"]
        db_count = await pool.fetchval("SELECT like_count FROM posts WHERE id = $1", post_id)
        updates = await posts_updates(pool) - updates_before

        return {
            "mode": mode,
            **k6_summary(summary),
            **locks,
            "posts_row_updates": updates,
            "likes_rows": truth,
            "api_like_count": api_count,
            "db_like_count": db_count,
            "db_settled_after_s": settle_s,
            "consistent": truth == api_count == db_count,
        }
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=15)
        await r.aclose()
        await pool.close()


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["direct", "buffered", "both"], default="both")
    p.add_argument("--rate", type=int, default=300, help="likes per second")
    p.add_argument("--duration", default="20s")
    p.add_argument("--users", type=int, default=0, help="default: rate * seconds * 1.2")
    p.add_argument("--workers", type=int, default=4, help="uvicorn worker processes")
    p.add_argument("--port", type=int, default=8100)
    p.add_argument("--k6", default="k6", help="path to the k6 binary")
    p.add_argument("--redis-url", default=settings.redis_url or "redis://localhost:6379/0")
    args = p.parse_args()

    refuse_remote(settings.database_url)
    seconds = float(args.duration.rstrip("s"))
    args.users = args.users or int(args.rate * seconds * 1.2) + 10
    # A throwaway signing secret unless one is configured: the API under test gets the same.
    # Always a throwaway secret: the API under test gets the same one, and tokens signed
    # with the real secret would be valid against the real deployment.
    secret = secrets.token_urlsafe(32)

    results = []
    for mode in (["direct", "buffered"] if args.mode == "both" else [args.mode]):
        print(f"--- {mode}: {args.rate} likes/s for {args.duration}, {args.workers} workers", flush=True)
        res = await run_mode(args, mode, secret)
        print(json.dumps(res, indent=2), flush=True)
        results.append(res)
    (LOADTEST / "out" / "results.json").write_text(json.dumps(results, indent=2))
    if not all(r["consistent"] for r in results):
        sys.exit("counts did not settle to the likes table")


if __name__ == "__main__":
    asyncio.run(main())
