"""Security, privacy and abuse hardening: RLS, rate limits, stream caps, id bounds,
upload size, cleanup of replaced photos."""

import uuid

import asyncpg
import pytest

from app import ratelimit
from app.config import settings
from tests.test_phase1 import auth_header, make_profile, s3  # noqa: F401 (fixture)


@pytest.fixture
async def db(database):
    conn = await asyncpg.connect(database)
    yield conn
    await conn.close()


@pytest.fixture
async def user(client):
    return await make_profile(client, f"hd_{uuid.uuid4().hex[:10]}")


@pytest.fixture
async def post_id(db, user):
    return await db.fetchval("INSERT INTO posts (author_id, image_key) VALUES ($1, 'seed/x.jpg') RETURNING id", user)


# --- Row-level security -----------------------------------------------------------


async def test_every_table_has_row_level_security(db):
    """Supabase serves the public schema over REST to anyone with the anon key."""
    off = await db.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND NOT rowsecurity")
    assert [r["tablename"] for r in off] == []


async def test_a_role_like_supabase_anon_sees_and_changes_nothing(db, user, post_id):
    # Stand-in for Supabase's `anon` role: granted everything on the tables, like
    # Supabase's default privileges, but not their owner.
    await db.execute("DROP ROLE IF EXISTS rls_probe; CREATE ROLE rls_probe NOLOGIN")
    await db.execute("GRANT ALL ON ALL TABLES IN SCHEMA public TO rls_probe")
    try:
        async with db.transaction():
            await db.execute("SET LOCAL ROLE rls_probe")
            assert await db.fetchval("SELECT count(*) FROM users") == 0
            assert await db.fetchval("SELECT count(*) FROM notifications") == 0
            assert await db.execute("UPDATE users SET website = 'javascript:alert(1)'") == "UPDATE 0"
            assert await db.execute("DELETE FROM posts") == "DELETE 0"
            with pytest.raises(asyncpg.InsufficientPrivilegeError):  # row-level security violation
                await db.execute("INSERT INTO follows (follower_id, followee_id) VALUES ($1, $1)", user)
    finally:
        await db.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM rls_probe; DROP ROLE rls_probe")
    assert await db.fetchval("SELECT count(*) FROM posts WHERE id = $1", post_id) == 1


# --- Rate limits -------------------------------------------------------------------


@pytest.fixture
def limits_on(monkeypatch):
    monkeypatch.setattr(settings, "rate_limits", True)


async def test_comment_rate_limit_is_per_user(client, limits_on, user, post_id):
    h = auth_header(user)
    for i in range(20):
        assert (await client.post(f"/posts/{post_id}/comments", json={"body": f"c{i}"}, headers=h)).status_code == 201
    resp = await client.post(f"/posts/{post_id}/comments", json={"body": "one too many"}, headers=h)
    assert resp.status_code == 429 and resp.headers["Retry-After"] == "60"
    other = await make_profile(client, f"hd_{uuid.uuid4().hex[:10]}")
    ok = await client.post(f"/posts/{post_id}/comments", json={"body": "me"}, headers=auth_header(other))
    assert ok.status_code == 201


async def test_limiter_fails_open_when_redis_breaks(client, limits_on, user, post_id, monkeypatch):
    async def broken(*a):
        raise ConnectionError("redis down")

    monkeypatch.setattr(ratelimit, "hit", broken)
    resp = await client.post(f"/posts/{post_id}/like", headers=auth_header(user))
    assert resp.status_code == 200


async def test_in_memory_limiter_without_redis():
    key = f"rl:test:{uuid.uuid4()}"
    assert [await ratelimit.hit(None, key, 60) for _ in range(3)] == [1, 2, 3]


# --- Streams, health, ids ----------------------------------------------------------


async def test_streams_are_capped(client, monkeypatch, post_id):
    monkeypatch.setattr(settings, "max_streams", 0)
    assert (await client.get(f"/stream?posts={post_id}")).status_code == 503


async def test_unstarted_stream_registers_nothing(post_id):
    """A client that leaves before the body starts must not leave a subscription behind."""
    from app.events import broker
    from app.main import stream

    before = broker.connections
    await stream(posts=str(post_id))  # response created, body never iterated
    assert broker.connections == before


async def test_healthz_touches_no_backing_store(client, monkeypatch):
    from app.main import app

    async def boom():
        raise AssertionError("must not ping Redis")

    monkeypatch.setattr(app.state.redis, "ping", boom)
    assert (await client.get("/healthz")).json() == {"status": "ok"}


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/posts/99999999999999999999"),
        ("get", "/posts/0"),
        ("post", "/posts/99999999999999999999/like"),
        ("get", "/posts/99999999999999999999/comments"),
        ("delete", "/comments/99999999999999999999"),
    ],
)
async def test_out_of_range_ids_are_client_errors(client, user, method, path):
    resp = await getattr(client, method)(path, headers=auth_header(user))
    assert resp.status_code == 422


async def test_out_of_range_ids_in_query_and_cursor(client, user):
    from app import cursor
    from datetime import datetime, timezone

    assert (await client.get("/likes?ids=99999999999999999999")).status_code == 400
    big = cursor.encode(datetime.now(timezone.utc), 2**70)
    assert (await client.get(f"/feed?before={big}", headers=auth_header(user))).status_code == 400


# --- Uploads -------------------------------------------------------------------------


async def upload(client, s3, user, purpose="post", body=b"img"):  # noqa: F811
    h = auth_header(user)
    up = (await client.post("/uploads", json={"content_type": "image/png", "purpose": purpose}, headers=h)).json()
    s3.put_object(Bucket=settings.s3_bucket, Key=up["key"], Body=body)
    return up["key"]


def exists(s3, key):  # noqa: F811
    return "Contents" in s3.list_objects_v2(Bucket=settings.s3_bucket, Prefix=key)


async def test_oversized_upload_is_refused_and_deleted(client, s3, user, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "max_upload_bytes", 10)
    key = await upload(client, s3, user, body=b"x" * 11)
    resp = await client.post("/posts", json={"image_key": key}, headers=auth_header(user))
    assert resp.status_code == 413
    assert not exists(s3, key)


async def test_replaced_and_removed_avatars_are_deleted(client, s3, user):  # noqa: F811
    h = auth_header(user)
    first = await upload(client, s3, user, "avatar")
    await client.patch("/me", json={"avatar_key": first}, headers=h)
    second = await upload(client, s3, user, "avatar")
    await client.patch("/me", json={"avatar_key": second}, headers=h)
    assert not exists(s3, first) and exists(s3, second)
    await client.patch("/me", json={"bio": "unrelated edit"}, headers=h)
    assert exists(s3, second)
    await client.patch("/me", json={"avatar_key": None}, headers=h)
    assert not exists(s3, second)
