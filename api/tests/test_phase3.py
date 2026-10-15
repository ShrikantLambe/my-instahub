"""Phase 3: follow/unfollow and the home feed with cursor pagination."""

import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from tests.test_phase1 import auth_header


@pytest.fixture
async def db(database):
    conn = await asyncpg.connect(database)
    yield conn
    await conn.close()


@pytest.fixture
async def make_user(client):
    async def _make(prefix="f"):
        user_id = uuid.uuid4()
        name = f"{prefix}_{user_id.hex[:10]}"
        resp = await client.put("/me", json={"username": name}, headers=auth_header(user_id))
        assert resp.status_code == 200, resp.text
        return user_id, name

    return _make


async def add_post(db, author, caption, created_at=None):
    return await db.fetchval(
        "INSERT INTO posts (author_id, caption, image_key, created_at) "
        "VALUES ($1, $2, 'seed/x.jpg', COALESCE($3, now())) RETURNING id",
        author,
        caption,
        created_at,
    )


async def follow(client, who, username):
    return await client.post(f"/users/{username}/follow", headers=auth_header(who))


async def unfollow(client, who, username):
    return await client.delete(f"/users/{username}/follow", headers=auth_header(who))


async def walk_feed(client, user, limit):
    """Follow next_cursor to the end; returns post ids in the order served."""
    ids, cur = [], None
    for _ in range(100):
        url = f"/feed?limit={limit}" + (f"&before={cur}" if cur else "")
        page = (await client.get(url, headers=auth_header(user))).json()
        ids += [p["id"] for p in page["posts"]]
        cur = page["next_cursor"]
        if cur is None:
            return ids
    raise AssertionError("feed never ended")


# --- follow ---


async def test_follow_is_idempotent_and_counts(client, make_user):
    (a, a_name), (b, _) = await make_user(), await make_user()
    first = (await follow(client, b, a_name)).json()
    again = (await follow(client, b, a_name)).json()
    assert first == again == {"username": a_name, "following": True, "follower_count": 1}

    profile = (await client.get(f"/users/{a_name}", headers=auth_header(b))).json()
    assert profile["viewer_follows"] is True and profile["follower_count"] == 1
    assert (await client.get(f"/users/{a_name}")).json()["viewer_follows"] is False  # anonymous

    assert (await unfollow(client, b, a_name)).json()["follower_count"] == 0
    assert (await unfollow(client, b, a_name)).json()["follower_count"] == 0  # idempotent
    assert (await client.get(f"/users/{a_name}", headers=auth_header(b))).json()["viewer_follows"] is False


async def test_follow_errors(client, make_user):
    me, my_name = await make_user()
    assert (await follow(client, me, my_name)).status_code == 400  # self
    assert (await follow(client, me, "no_such_user_xyz")).status_code == 404
    assert (await follow(client, uuid.uuid4(), my_name)).status_code == 409  # no profile yet
    assert (await client.post(f"/users/{my_name}/follow")).status_code == 401


async def test_database_refuses_self_follow(db, make_user):
    me, _ = await make_user()
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("INSERT INTO follows VALUES ($1, $1)", me)


# --- feed contents ---


async def test_feed_has_followed_and_own_posts_only(client, db, make_user):
    (me, _), (friend, friend_name), (stranger, _) = await make_user(), await make_user(), await make_user()
    mine = await add_post(db, me, "mine")
    theirs = await add_post(db, friend, "friend's")
    await add_post(db, stranger, "stranger's")

    assert await walk_feed(client, me, 10) == [mine]
    await follow(client, me, friend_name)
    assert await walk_feed(client, me, 10) == [theirs, mine]  # newest first
    await unfollow(client, me, friend_name)
    assert await walk_feed(client, me, 10) == [mine]


async def test_feed_requires_auth_and_valid_cursor(client, make_user):
    me, _ = await make_user()
    assert (await client.get("/feed")).status_code == 401
    for bad in ["garbage!!", "bm90LWEtY3Vyc29y", "MjAyNi0xMC0xNVQwMDowMDowMHwx"]:  # junk, no '|', naive ts
        assert (await client.get(f"/feed?before={bad}", headers=auth_header(me))).status_code == 400


async def test_feed_marks_viewer_likes(client, db, make_user):
    me, _ = await make_user()
    pid = await add_post(db, me, "like me")
    await client.post(f"/posts/{pid}/like", headers=auth_header(me))
    page = (await client.get("/feed", headers=auth_header(me))).json()
    assert page["posts"][0]["viewer_has_liked"] is True


# --- cursor pagination ---


async def test_pagination_walks_everything_once_in_order(client, db, make_user):
    (me, _), (f1, n1), (f2, n2) = await make_user(), await make_user(), await make_user()
    await follow(client, me, n1)
    await follow(client, me, n2)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expected = []
    for i in range(23):
        author = (me, f1, f2)[i % 3]
        expected.append(await add_post(db, author, f"p{i}", base + timedelta(minutes=i)))
    expected.reverse()  # newest first

    for limit in (1, 5, 10, 23, 50):
        assert await walk_feed(client, me, limit) == expected, f"limit={limit}"


async def test_new_posts_mid_scroll_do_not_shift_pages(client, db, make_user):
    """The offset bug: with OFFSET, a new post pushes items down and page 2 repeats one."""
    (me, _), (friend, name) = await make_user(), await make_user()
    await follow(client, me, name)
    base = datetime(2026, 2, 1, tzinfo=timezone.utc)
    old = [await add_post(db, friend, f"old{i}", base + timedelta(minutes=i)) for i in range(6)]

    page1 = (await client.get("/feed?limit=3", headers=auth_header(me))).json()
    await add_post(db, friend, "brand new")  # arrives while the user is scrolling
    page2 = (
        await client.get(f"/feed?limit=3&before={page1['next_cursor']}", headers=auth_header(me))
    ).json()

    seen = [p["id"] for p in page1["posts"] + page2["posts"]]
    assert seen == old[::-1]  # all six old posts, once each, in order
    assert page2["next_cursor"] is None


async def test_identical_timestamps_tie_break_on_id(client, db, make_user):
    me, _ = await make_user()
    same = datetime(2026, 3, 1, tzinfo=timezone.utc)
    ids = [await add_post(db, me, f"same{i}", same) for i in range(7)]
    assert await walk_feed(client, me, 2) == sorted(ids, reverse=True)
