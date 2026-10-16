"""Phase 6: buffered like counter, batched flush, reconciliation."""

import asyncio
import uuid

import asyncpg
import pytest

from app import counter
from app.config import settings
from tests.test_phase1 import auth_header


@pytest.fixture
def buffered(monkeypatch):
    monkeypatch.setattr(settings, "counter_mode", "buffered")


@pytest.fixture
def redis(client):
    from app.main import app

    return app.state.redis


@pytest.fixture
async def db(database):
    conn = await asyncpg.connect(database)
    yield conn
    await conn.close()


@pytest.fixture
def pool(client):
    from app import db as dbmod

    return dbmod.get_pool()


@pytest.fixture
async def make_user(client):
    async def _make():
        user_id = uuid.uuid4()
        resp = await client.put("/me", json={"username": f"p6_{user_id.hex[:10]}"}, headers=auth_header(user_id))
        assert resp.status_code == 200, resp.text
        return user_id

    return _make


@pytest.fixture
async def post_id(db, make_user):
    return await db.fetchval(
        "INSERT INTO posts (author_id, caption, image_key) VALUES ($1, 'p6', 'seed/x.jpg') RETURNING id",
        await make_user(),
    )


async def like(client, pid, user):
    return await client.post(f"/posts/{pid}/like", headers=auth_header(user))


async def unlike(client, pid, user):
    return await client.delete(f"/posts/{pid}/like", headers=auth_header(user))


async def stored(db, pid):
    return await db.fetchrow(
        "SELECT like_count, like_version, (SELECT count(*) FROM likes WHERE post_id = $1) AS truth "
        "FROM posts WHERE id = $1",
        pid,
    )


async def test_health_reports_mode(client, buffered):
    assert (await client.get("/health")).json()["counter_mode"] == "buffered"


async def test_buffered_like_counts_live_and_flushes_later(client, buffered, redis, pool, db, post_id, make_user):
    user = await make_user()
    body = (await like(client, post_id, user)).json()
    assert body["like_count"] == 1 and body["liked"] is True
    row = await stored(db, post_id)
    assert row["truth"] == 1 and row["like_count"] == 0  # Postgres counter not touched yet
    assert await redis.get(counter.pending_key(post_id)) == "1"

    # Reads show the live count even before the flush.
    assert (await client.get(f"/posts/{post_id}")).json()["like_count"] == 1

    assert await counter.flush(redis, pool) == 1
    row = await stored(db, post_id)
    assert row["like_count"] == row["truth"] == 1
    assert row["like_version"] >= body["like_version"]
    assert await redis.get(counter.pending_key(post_id)) is None


async def test_buffered_is_still_idempotent(client, buffered, redis, pool, db, post_id, make_user):
    user = await make_user()
    responses = await asyncio.gather(*(like(client, post_id, user) for _ in range(10)))
    assert {r.json()["like_count"] for r in responses} == {1}
    assert (await unlike(client, post_id, user)).json()["like_count"] == 0
    assert (await unlike(client, post_id, user)).json()["like_count"] == 0
    await counter.flush(redis, pool)
    row = await stored(db, post_id)
    assert row["like_count"] == row["truth"] == 0


async def test_buffered_hot_post_many_likers(client, buffered, redis, pool, db, post_id, make_user):
    users = [await make_user() for _ in range(40)]
    await asyncio.gather(*(like(client, post_id, u) for u in users))
    assert (await client.get(f"/likes?ids={post_id}")).json()[0]["like_count"] == 40
    await counter.flush(redis, pool)
    row = await stored(db, post_id)
    assert row["like_count"] == row["truth"] == 40


async def test_versions_continue_from_direct_mode(client, monkeypatch, redis, db, post_id, make_user):
    """Switching modes must not make versions go backwards (clients would ignore updates)."""
    first = (await like(client, post_id, await make_user())).json()  # direct mode
    monkeypatch.setattr(settings, "counter_mode", "buffered")
    second = (await like(client, post_id, await make_user())).json()
    assert second["like_count"] == 2
    assert second["like_version"] > first["like_version"]


async def test_live_count_rebuilds_from_truth_if_redis_loses_it(client, buffered, redis, pool, db, post_id, make_user):
    await like(client, post_id, await make_user())
    await like(client, post_id, await make_user())
    await counter.flush(redis, pool)
    await redis.delete(counter.count_key(post_id), counter.version_key(post_id))  # evicted
    assert (await client.get(f"/likes?ids={post_id}")).json()[0]["like_count"] == 2


async def test_failed_flush_puts_deltas_back(client, buffered, redis, pool, db, post_id, make_user, monkeypatch):
    await like(client, post_id, await make_user())

    async def boom(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(counter, "_apply", boom)
    with pytest.raises(RuntimeError):
        await counter.flush(redis, pool)
    assert await redis.get(counter.pending_key(post_id)) == "1"  # nothing lost
    assert await redis.sismember(counter.DIRTY, post_id)

    monkeypatch.undo()
    monkeypatch.setattr(settings, "counter_mode", "buffered")
    await counter.flush(redis, pool)
    row = await stored(db, post_id)
    assert row["like_count"] == row["truth"] == 1


async def test_likes_during_flush_are_not_lost(client, buffered, redis, pool, db, post_id, make_user, monkeypatch):
    """A like that lands while a flush is writing Postgres stays pending for the next one."""
    await like(client, post_id, await make_user())
    real_apply = counter._apply
    late_user = await make_user()

    async def slow_apply(p, rows):
        await like(client, post_id, late_user)  # arrives mid-flush
        await real_apply(p, rows)

    monkeypatch.setattr(counter, "_apply", slow_apply)
    await counter.flush(redis, pool)
    monkeypatch.setattr(counter, "_apply", real_apply)
    assert await redis.get(counter.pending_key(post_id)) == "1"
    await counter.flush(redis, pool)
    row = await stored(db, post_id)
    assert row["like_count"] == row["truth"] == 2
    assert (await client.get(f"/likes?ids={post_id}")).json()[0]["like_count"] == 2


async def test_first_touch_race_counts_each_like_once(client, buffered, redis, pool, db, post_id, make_user):
    """Many first likes at once on a post Redis has never seen: each counted exactly once."""
    for _ in range(5):  # repeat: the race is timing dependent
        users = [await make_user() for _ in range(30)]
        await redis.delete(counter.count_key(post_id), counter.version_key(post_id))
        await asyncio.gather(*(like(client, post_id, u) for u in users))
        await asyncio.gather(*(unlike(client, post_id, u) for u in users))
        assert (await client.get(f"/likes?ids={post_id}")).json()[0]["like_count"] == 0
    await counter.flush(redis, pool)
    row = await stored(db, post_id)
    assert row["like_count"] == row["truth"] == 0


async def test_first_touch_does_not_queue_behind_the_lock(client, buffered, redis, pool, db, post_id, make_user):
    """A cold post's first wave of likes must not wait for the lock once a count exists.

    Under load, hundreds of first-touch likes used to queue on the lock one by one and
    time out. Here the lock is held (as by a long flush) while another request has
    already started the count: a new first-touch like must read it and return.
    """
    user = await make_user()
    await redis.set(counter.LOCK, "someone-else", px=10_000)
    try:
        # Our like finds no count (BUMP returns nil), then the count appears meanwhile.
        await redis.delete(counter.count_key(post_id), counter.version_key(post_id))
        real_eval = redis.eval

        async def eval_then_start(script, *a):
            result = await real_eval(script, *a)
            if script == counter.BUMP and result is None:
                await real_eval(counter.INIT, 3, counter.count_key(post_id), counter.version_key(post_id),
                                counter.pending_key(post_id), 0, 7)
            return result

        redis.eval = eval_then_start
        try:
            resp = await asyncio.wait_for(like(client, post_id, user), timeout=2)
        finally:
            del redis.eval
    finally:
        await redis.delete(counter.LOCK)
    assert resp.status_code == 200, resp.text
    assert resp.json()["like_count"] == 1


async def test_reconcile_repairs_a_lost_delta(client, buffered, redis, pool, db, post_id, make_user):
    """The crash case: a delta taken from Redis but never committed to Postgres."""
    for _ in range(3):
        await like(client, post_id, await make_user())
    await redis.delete(counter.pending_key(post_id))  # "taken", then the process died
    await redis.srem(counter.DIRTY, post_id)
    assert (await stored(db, post_id))["like_count"] == 0  # drifted: truth is 3

    repaired = await counter.reconcile(pool, redis)
    assert any(r["post_id"] == post_id for r in repaired)
    row = await stored(db, post_id)
    assert row["like_count"] == row["truth"] == 3
    assert await counter.reconcile(pool, redis) == [] or all(r["post_id"] != post_id for r in await counter.reconcile(pool, redis))


async def test_reconcile_accounts_for_pending(client, buffered, redis, pool, db, post_id, make_user):
    """Unflushed deltas are not drift: reconcile must not double count them."""
    await like(client, post_id, await make_user())
    await like(client, post_id, await make_user())  # 2 pending, posts.like_count still 0
    repaired = await counter.reconcile(pool, redis)
    assert all(r["post_id"] != post_id for r in repaired)
    await counter.flush(redis, pool)
    row = await stored(db, post_id)
    assert row["like_count"] == row["truth"] == 2


async def test_reconcile_fixes_direct_mode_drift(pool, db, post_id):
    await db.execute("UPDATE posts SET like_count = 99 WHERE id = $1", post_id)  # corrupted copy
    await counter.reconcile(pool, None)
    row = await stored(db, post_id)
    assert row["like_count"] == row["truth"] == 0
