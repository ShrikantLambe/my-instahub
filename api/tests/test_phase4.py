"""Phase 4: versioned cache-aside like counts, pub/sub fan-out, SSE stream."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import asyncpg
import httpx
import pytest
import redis.asyncio as aioredis

from app import cache
from app.events import Broker
from app.likes import LikeCount
from tests.test_phase1 import auth_header


@pytest.fixture
async def make_user(client):
    async def _make():
        user_id = uuid.uuid4()
        resp = await client.put("/me", json={"username": f"rt_{user_id.hex[:10]}"}, headers=auth_header(user_id))
        assert resp.status_code == 200, resp.text
        return user_id

    return _make


@pytest.fixture
async def post_id(database, make_user):
    author = await make_user()
    conn = await asyncpg.connect(database)
    try:
        return await conn.fetchval(
            "INSERT INTO posts (author_id, caption, image_key) VALUES ($1, 'rt', 'seed/x.jpg') RETURNING id", author
        )
    finally:
        await conn.close()


@pytest.fixture
def redis(client):
    from app.main import app

    return app.state.redis


async def test_like_writes_versioned_count_to_cache(client, redis, post_id, make_user):
    user = await make_user()
    body = (await client.post(f"/posts/{post_id}/like", headers=auth_header(user))).json()
    assert body["like_count"] == 1 and body["like_version"] == 1
    assert await redis.get(cache.key(post_id)) == "1:1"
    await client.delete(f"/posts/{post_id}/like", headers=auth_header(user))
    assert await redis.get(cache.key(post_id)) == "2:0"  # version 2, count 0


async def test_cache_never_goes_backwards(redis, post_id):
    assert await cache.store(redis, post_id, LikeCount(count=5, version=7))
    assert not await cache.store(redis, post_id, LikeCount(count=4, version=6))  # late, older write
    assert not await cache.store(redis, post_id, LikeCount(count=4, version=7))  # same version
    assert await redis.get(cache.key(post_id)) == "7:5"
    assert await cache.store(redis, post_id, LikeCount(count=6, version=8))
    assert await redis.get(cache.key(post_id)) == "8:6"


async def test_likes_endpoint_is_cache_aside(client, redis, post_id, make_user):
    await client.post(f"/posts/{post_id}/like", headers=auth_header(await make_user()))
    await redis.delete(cache.key(post_id))  # expired / evicted
    resp = await client.get(f"/likes?ids={post_id}")
    assert resp.json() == [{"post_id": post_id, "like_count": 1, "like_version": 1}]
    assert await redis.get(cache.key(post_id)) == "1:1"  # miss refilled from Postgres
    ttl = await redis.ttl(cache.key(post_id))
    assert 0 < ttl <= cache.TTL_SECONDS


async def test_likes_endpoint_validates_ids(client):
    for bad in ["", "a,b", ",".join(str(i) for i in range(101))]:
        assert (await client.get(f"/likes?ids={bad}")).status_code in (400, 422)


async def test_redis_outage_does_not_break_likes(client, post_id, make_user, monkeypatch):
    """Postgres is the truth: with Redis unreachable, likes and count reads still work."""
    from app.main import app

    dead = aioredis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    monkeypatch.setattr(app.state, "redis", dead)
    user = await make_user()
    assert (await client.post(f"/posts/{post_id}/like", headers=auth_header(user))).json()["like_count"] == 1
    assert (await client.get(f"/likes?ids={post_id}")).json()[0]["like_count"] == 1
    await dead.aclose()


async def test_concurrent_likes_leave_cache_at_final_version(client, redis, database, post_id, make_user):
    users = [await make_user() for _ in range(15)]
    await asyncio.gather(*(client.post(f"/posts/{post_id}/like", headers=auth_header(u)) for u in users))
    conn = await asyncpg.connect(database)
    row = await conn.fetchrow("SELECT like_count, like_version FROM posts WHERE id = $1", post_id)
    await conn.close()
    assert (row["like_count"], row["like_version"]) == (15, 15)
    assert await redis.get(cache.key(post_id)) == "15:15"


async def test_like_is_published_through_redis(client, post_id, make_user):
    from app.events import broker

    sub = broker.subscribe({post_id})
    try:
        await client.post(f"/posts/{post_id}/like", headers=auth_header(await make_user()))
        event = await asyncio.wait_for(sub.queue.get(), timeout=3)
        assert event == {"post_id": post_id, "like_count": 1, "like_version": 1}
    finally:
        broker.unsubscribe(sub)


async def test_repeat_like_publishes_nothing(client, post_id, make_user):
    from app.events import broker

    user = await make_user()
    await client.post(f"/posts/{post_id}/like", headers=auth_header(user))
    await asyncio.sleep(0.2)
    sub = broker.subscribe({post_id})
    try:
        await client.post(f"/posts/{post_id}/like", headers=auth_header(user))  # no-op
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sub.queue.get(), timeout=0.5)
    finally:
        broker.unsubscribe(sub)


async def test_local_broker_filters_by_post():
    b = Broker()
    await b.start(None)  # no Redis: in-process delivery
    watching, other = b.subscribe({1, 2}), b.subscribe({3})
    await b.publish({"post_id": 2, "like_count": 9, "like_version": 4})
    assert watching.queue.get_nowait()["like_count"] == 9
    assert other.queue.empty()


# --- the real HTTP stream, served by uvicorn (httpx's in-memory transport can't stream) ---


@pytest.fixture
def live_server(database):
    """A separate API process on the same Postgres and Redis, like a second instance."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=Path(__file__).resolve().parent.parent,
        env={**os.environ, "DATABASE_URL": database},
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{url}/health", timeout=0.5).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=5)


async def read_events(lines, n):
    """Read n events from one shared line iterator (a stream can only be iterated once)."""
    events, data, name = [], None, None
    async for line in lines:
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
        elif line == "" and data is not None:
            events.append((name, data))
            data = None
            if len(events) == n:
                return events
    return events


async def test_sse_stream_snapshot_then_live(live_server, client, post_id, make_user):
    async with httpx.AsyncClient(base_url=live_server, timeout=10) as http:
        async with http.stream("GET", f"/stream?posts={post_id}") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            lines = resp.aiter_lines()
            snapshot = await asyncio.wait_for(read_events(lines, 1), timeout=5)
            assert snapshot == [("likes", {"post_id": post_id, "like_count": 0, "like_version": 0})]

            # The like is handled by *this* test process's app; the stream is served by the
            # other process. Redis pub/sub is what carries it across, as between instances.
            liker = await make_user()
            await client.post(f"/posts/{post_id}/like", headers=auth_header(liker))
            live = await asyncio.wait_for(read_events(lines, 1), timeout=5)
            assert live == [("likes", {"post_id": post_id, "like_count": 1, "like_version": 1})]
