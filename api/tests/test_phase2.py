"""Phase 2: idempotent likes, like_count, viewer_has_liked, concurrency."""

import asyncio
import random
import uuid

import asyncpg
import pytest

from tests.test_phase1 import auth_header


@pytest.fixture
async def make_user(client):
    async def _make(prefix="u"):
        user_id = uuid.uuid4()
        name = f"{prefix}_{user_id.hex[:10]}"
        resp = await client.put("/me", json={"username": name}, headers=auth_header(user_id))
        assert resp.status_code == 200, resp.text
        return user_id

    return _make


@pytest.fixture
async def post_id(database, make_user):
    """A fresh post with no likes (inserted directly; uploads are Phase 1's concern)."""
    author = await make_user("author")
    conn = await asyncpg.connect(database)
    try:
        return await conn.fetchval(
            "INSERT INTO posts (author_id, caption, image_key) VALUES ($1, 'p2', 'seed/x.jpg') RETURNING id",
            author,
        )
    finally:
        await conn.close()


async def db_counts(database, pid):
    """(denormalized like_count, true COUNT(*) from likes)."""
    conn = await asyncpg.connect(database)
    try:
        return await conn.fetchrow(
            "SELECT like_count, (SELECT count(*) FROM likes WHERE post_id = $1) AS truth "
            "FROM posts WHERE id = $1",
            pid,
        )
    finally:
        await conn.close()


async def like(client, pid, user):
    return await client.post(f"/posts/{pid}/like", headers=auth_header(user))


async def unlike(client, pid, user):
    return await client.delete(f"/posts/{pid}/like", headers=auth_header(user))


async def test_like_requires_auth(client, post_id):
    assert (await client.post(f"/posts/{post_id}/like")).status_code == 401
    assert (await client.delete(f"/posts/{post_id}/like")).status_code == 401


async def test_like_missing_post_is_404(client, make_user):
    user = await make_user()
    assert (await like(client, 999_999_999, user)).status_code == 404
    assert (await unlike(client, 999_999_999, user)).status_code == 404


async def test_like_without_profile_is_409(client, post_id):
    resp = await like(client, post_id, uuid.uuid4())  # valid token, no username yet
    assert resp.status_code == 409


async def test_like_twice_counts_once(client, database, post_id, make_user):
    user = await make_user()
    first = (await like(client, post_id, user)).json()
    second = (await like(client, post_id, user)).json()  # double-tap / retry
    assert first == {"post_id": post_id, "liked": True, "like_count": 1, "like_version": 1}
    assert second == first
    row = await db_counts(database, post_id)
    assert row["like_count"] == row["truth"] == 1


async def test_unlike_is_idempotent_and_never_negative(client, database, post_id, make_user):
    user = await make_user()
    assert (await unlike(client, post_id, user)).json()["like_count"] == 0  # never liked
    await like(client, post_id, user)
    assert (await unlike(client, post_id, user)).json() == {
        "post_id": post_id, "liked": False, "like_count": 0, "like_version": 2
    }
    assert (await unlike(client, post_id, user)).json()["like_count"] == 0
    row = await db_counts(database, post_id)
    assert row["like_count"] == row["truth"] == 0


async def test_viewer_has_liked(client, post_id, make_user):
    liker, other = await make_user(), await make_user()
    await like(client, post_id, liker)

    def find(posts):
        return next(p for p in posts if p["id"] == post_id)

    # Feed: per-viewer flag; anonymous or a bad token just reads as "not liked".
    assert find((await client.get("/posts?limit=50", headers=auth_header(liker))).json())["viewer_has_liked"]
    assert not find((await client.get("/posts?limit=50", headers=auth_header(other))).json())["viewer_has_liked"]
    assert not find((await client.get("/posts?limit=50")).json())["viewer_has_liked"]
    bad = {"Authorization": "Bearer nonsense"}
    resp = await client.get("/posts?limit=50", headers=bad)
    assert resp.status_code == 200 and not find(resp.json())["viewer_has_liked"]

    # Single post agrees, and so does the count.
    single = (await client.get(f"/posts/{post_id}", headers=auth_header(liker))).json()
    assert single["viewer_has_liked"] and single["like_count"] == 1


async def test_many_users_like_at_once(client, database, post_id, make_user):
    """Concurrent likes from different users all land; none are lost."""
    users = [await make_user() for _ in range(25)]
    responses = await asyncio.gather(*(like(client, post_id, u) for u in users))
    assert all(r.status_code == 200 for r in responses)
    row = await db_counts(database, post_id)
    assert row["like_count"] == row["truth"] == 25


async def test_same_user_concurrent_double_taps(client, database, post_id, make_user):
    """Ten simultaneous likes from one user (a frantic double-tap) still count once."""
    user = await make_user()
    responses = await asyncio.gather(*(like(client, post_id, user) for _ in range(10)))
    assert {r.json()["like_count"] for r in responses} == {1}
    row = await db_counts(database, post_id)
    assert row["like_count"] == row["truth"] == 1


async def test_counter_matches_truth_after_random_storm(client, database, post_id, make_user):
    """Interleaved likes and unlikes from many users: the copy always equals the truth."""
    users = [await make_user() for _ in range(8)]
    rng = random.Random(42)
    ops = [rng.choice((like, unlike))(client, post_id, rng.choice(users)) for _ in range(120)]
    responses = await asyncio.gather(*ops)
    assert all(r.status_code == 200 for r in responses)
    row = await db_counts(database, post_id)
    assert row["like_count"] == row["truth"]
    assert 0 <= row["like_count"] <= len(users)
