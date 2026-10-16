"""Phase 10: fan-out-on-write home feed (FEED_MODE=write) with the celebrity hybrid."""

import random
import uuid

import pytest

from app import feed
from app.config import settings
from tests.test_phase1 import auth_header, s3  # noqa: F401 (fixture)


@pytest.fixture
def redis(client):
    from app.main import app

    return app.state.redis


@pytest.fixture
def pool(client):
    from app import db

    return db.get_pool()


@pytest.fixture
def write_mode(monkeypatch):
    monkeypatch.setattr(settings, "feed_mode", "write")


@pytest.fixture
async def make_user(client):
    async def _make():
        user_id = uuid.uuid4()
        name = f"fo_{user_id.hex[:10]}"
        resp = await client.put("/me", json={"username": name}, headers=auth_header(user_id))
        assert resp.status_code == 200, resp.text
        return user_id, name

    return _make


@pytest.fixture
def add_post(pool, redis):
    """Insert a post and fan it out, as POST /posts does (minus the photo upload)."""

    async def _add(author, caption="p"):
        post_id = await pool.fetchval(
            "INSERT INTO posts (author_id, caption, image_key) VALUES ($1, $2, 'seed/x.jpg') RETURNING id",
            author,
            caption,
        )
        if settings.feed_mode == "write":
            await feed.on_post(redis, pool, post_id, author, settings.fanout_max_followers)
        return post_id

    return _add


async def follow(client, who, username, on=True):
    resp = await (client.post if on else client.delete)(f"/users/{username}/follow", headers=auth_header(who))
    assert resp.status_code == 200, resp.text


async def walk(client, user, limit):
    ids, cur = [], None
    for _ in range(200):
        url = f"/feed?limit={limit}" + (f"&before={cur}" if cur else "")
        page = (await client.get(url, headers=auth_header(user))).json()
        ids += [p["id"] for p in page["posts"]]
        if not (cur := page["next_cursor"]):
            return ids
    raise AssertionError("feed never ended")


async def test_health_reports_mode(client, write_mode):
    assert (await client.get("/health")).json()["feed_mode"] == "write"


async def test_same_feed_as_fan_out_on_read(client, monkeypatch, make_user, add_post):
    reader, _ = await make_user()
    authors = [await make_user() for _ in range(6)]
    for uid, name in authors[:4]:
        await follow(client, reader, name)
    rng = random.Random(7)
    for i in range(40):
        await add_post(rng.choice(authors + [(reader, None)])[0], f"post {i}")
    read_mode = await walk(client, reader, 7)

    monkeypatch.setattr(settings, "feed_mode", "write")
    assert await walk(client, reader, 7) == read_mode  # first walk builds the feed
    await add_post(authors[0][0], "fresh")  # now fanned out on write
    monkeypatch.setattr(settings, "feed_mode", "read")
    expected = await walk(client, reader, 7)
    monkeypatch.setattr(settings, "feed_mode", "write")
    assert await walk(client, reader, 7) == expected
    assert len(expected) == len(read_mode) + 1


async def test_new_post_reaches_followers_feeds(client, redis, write_mode, make_user, add_post):
    author, name = await make_user()
    fan, outsider = (await make_user())[0], (await make_user())[0]
    await follow(client, fan, name)
    await walk(client, fan, 10)  # fan is active: their feed exists
    post = await add_post(author)
    assert await redis.zscore(feed.key(fan), feed.member(post)) is not None
    assert (await walk(client, fan, 10))[0] == post
    assert await redis.exists(feed.key(outsider)) == 0  # never read a feed: nothing stored
    assert post not in await walk(client, outsider, 10)


async def test_follow_backfills_and_unfollow_removes(client, redis, write_mode, make_user, add_post):
    author, name = await make_user()
    old = [await add_post(author) for _ in range(3)]
    reader = (await make_user())[0]
    assert await walk(client, reader, 10) == []
    await follow(client, reader, name)
    assert await walk(client, reader, 10) == old[::-1]
    await follow(client, reader, name, on=False)
    assert await walk(client, reader, 10) == []
    assert await redis.zcard(feed.key(reader)) == 1  # just the sentinel


async def test_celebrities_are_merged_at_read_time(client, redis, monkeypatch, write_mode, make_user, add_post):
    monkeypatch.setattr(settings, "fanout_max_followers", 2)
    celeb, celeb_name = await make_user()
    normal, normal_name = await make_user()
    fans = [(await make_user())[0] for _ in range(3)]
    for f in fans:
        await follow(client, f, celeb_name)
        await walk(client, f, 5)
    await follow(client, fans[0], normal_name)

    a = await add_post(normal)
    b = await add_post(celeb)  # 3 followers > 2: not fanned out
    c = await add_post(normal)
    assert await redis.sismember(feed.CELEBS, str(celeb))
    assert await redis.zscore(feed.key(fans[0]), feed.member(b)) is None
    assert await walk(client, fans[0], 2) == [c, b, a]  # merged in order, across pages
    assert await walk(client, fans[1], 2) == [b]


async def test_rebuilds_when_redis_loses_a_feed(client, redis, write_mode, make_user, add_post):
    author, name = await make_user()
    reader = (await make_user())[0]
    await follow(client, reader, name)
    posts = [await add_post(author) for _ in range(4)]
    before = await walk(client, reader, 3)
    await redis.delete(feed.key(reader))  # evicted or expired
    assert await walk(client, reader, 3) == before == posts[::-1]


async def test_older_than_the_window_comes_from_postgres(client, monkeypatch, write_mode, make_user, add_post):
    monkeypatch.setattr(feed, "FEED_LEN", 10)
    author, name = await make_user()
    reader = (await make_user())[0]
    await follow(client, reader, name)
    await walk(client, reader, 4)
    posts = [await add_post(author) for _ in range(25)]
    assert await walk(client, reader, 4) == posts[::-1]


async def test_create_post_fans_out_and_survives_redis_errors(client, redis, s3, monkeypatch, write_mode, make_user):  # noqa: F811
    author, name = await make_user()
    fan = (await make_user())[0]
    await follow(client, fan, name)
    await walk(client, fan, 5)

    async def create():
        h = auth_header(author)
        up = (await client.post("/uploads", json={"content_type": "image/png"}, headers=h)).json()
        s3.put_object(Bucket=settings.s3_bucket, Key=up["key"], Body=b"x")
        resp = await client.post("/posts", json={"image_key": up["key"]}, headers=h)
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    first = await create()
    assert await redis.zscore(feed.key(fan), feed.member(first)) is not None

    async def broken(*a):
        raise ConnectionError("redis went away")

    monkeypatch.setattr(feed, "on_post", broken)
    await create()  # still 201: the feed is a cache


async def test_order_matches_postgres_even_when_ids_and_times_disagree(client, pool, redis, monkeypatch, make_user):
    """Backdated posts (ids out of time order) and same-instant posts (tie broken by id)
    must page identically in both modes, through fan-out and through a rebuild."""
    from datetime import datetime, timedelta, timezone

    author, name = await make_user()
    reader = (await make_user())[0]
    await follow(client, reader, name)
    monkeypatch.setattr(settings, "feed_mode", "write")
    await walk(client, reader, 3)  # the reader's feed exists, so posts are fanned out to it

    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stamps = [t] * 5 + [t - timedelta(minutes=m) for m in (3, 1, 2)] + [t + timedelta(seconds=1)] * 2
    for ts in stamps:
        post_id = await pool.fetchval(
            "INSERT INTO posts (author_id, image_key, created_at) VALUES ($1, 'seed/x.jpg', $2) RETURNING id", author, ts
        )
        await feed.on_post(redis, pool, post_id, author, settings.fanout_max_followers)

    fanned = await walk(client, reader, 3)
    await redis.delete(feed.key(reader))
    rebuilt = await walk(client, reader, 3)
    monkeypatch.setattr(settings, "feed_mode", "read")
    expected = await walk(client, reader, 3)
    assert len(expected) == 10
    assert fanned == rebuilt == expected
