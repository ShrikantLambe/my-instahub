"""Phase 7: comments. Add, delete, page, keep posts.comment_count exact, publish live."""

import asyncio
import uuid

import asyncpg
import httpx
import pytest

from tests.test_phase1 import auth_header
from tests.test_phase4 import live_server, read_events  # noqa: F401 (fixture)


@pytest.fixture
async def make_user(client):
    async def _make():
        user_id = uuid.uuid4()
        resp = await client.put("/me", json={"username": f"cm_{user_id.hex[:10]}"}, headers=auth_header(user_id))
        assert resp.status_code == 200, resp.text
        return user_id

    return _make


@pytest.fixture
async def db(database):
    conn = await asyncpg.connect(database)
    yield conn
    await conn.close()


@pytest.fixture
async def author(make_user):
    return await make_user()


@pytest.fixture
async def post_id(db, author):
    return await db.fetchval(
        "INSERT INTO posts (author_id, caption, image_key) VALUES ($1, 'cm', 'seed/x.jpg') RETURNING id", author
    )


async def comment(client, pid, user, body="nice"):
    return await client.post(f"/posts/{pid}/comments", json={"body": body}, headers=auth_header(user))


async def stored(db, pid):
    return await db.fetchrow(
        "SELECT comment_count, comment_version, (SELECT count(*) FROM comments WHERE post_id = $1) AS truth "
        "FROM posts WHERE id = $1",
        pid,
    )


async def test_add_and_list_newest_first(client, db, post_id, make_user):
    alice = await make_user()
    first = await comment(client, post_id, alice, "  first!  ")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["comment"]["body"] == "first!"  # trimmed
    assert body["comment"]["author"]["id"] == str(alice)
    assert (body["comment_count"], body["comment_version"]) == (1, 1)
    await comment(client, post_id, alice, "second")

    page = (await client.get(f"/posts/{post_id}/comments")).json()
    assert [c["body"] for c in page["comments"]] == ["second", "first!"]
    assert (page["comment_count"], page["next_cursor"]) == (2, None)
    post = (await client.get(f"/posts/{post_id}")).json()
    assert (post["comment_count"], post["comment_version"]) == (2, 2)


async def test_needs_login_and_profile(client, post_id):
    assert (await client.post(f"/posts/{post_id}/comments", json={"body": "hi"})).status_code == 401
    no_profile = await comment(client, post_id, uuid.uuid4())
    assert no_profile.status_code == 409


@pytest.mark.parametrize("text", ["", "   ", "x" * 2201])
async def test_rejects_empty_or_too_long(client, post_id, make_user, text):
    assert (await comment(client, post_id, await make_user(), text)).status_code == 422


async def test_unknown_post(client, make_user):
    assert (await comment(client, 999_999_999, await make_user())).status_code == 404
    assert (await client.get("/posts/999999999/comments")).status_code == 404


async def test_delete_rules(client, db, post_id, author, make_user):
    commenter, stranger = await make_user(), await make_user()
    a = (await comment(client, post_id, commenter, "mine")).json()["comment"]["id"]
    b = (await comment(client, post_id, commenter, "also mine")).json()["comment"]["id"]

    assert (await client.delete(f"/comments/{a}", headers=auth_header(stranger))).status_code == 403
    assert (await client.delete(f"/comments/{a}")).status_code == 401

    own = await client.delete(f"/comments/{a}", headers=auth_header(commenter))
    assert own.status_code == 200
    assert own.json()["deleted_id"] == a and own.json()["comment_count"] == 1
    by_post_author = await client.delete(f"/comments/{b}", headers=auth_header(author))
    assert by_post_author.status_code == 200 and by_post_author.json()["comment_count"] == 0
    assert (await client.delete(f"/comments/{a}", headers=auth_header(commenter))).status_code == 404

    row = await stored(db, post_id)
    assert (row["comment_count"], row["truth"], row["comment_version"]) == (0, 0, 4)


async def test_cursor_pages_cover_everything_once(client, post_id, make_user):
    user = await make_user()
    for i in range(25):
        await comment(client, post_id, user, f"c{i}")
    seen, cursor = [], None
    while True:
        qs = "limit=10" + (f"&before={cursor}" if cursor else "")
        page = (await client.get(f"/posts/{post_id}/comments?{qs}")).json()
        seen += [c["body"] for c in page["comments"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [f"c{i}" for i in reversed(range(25))]
    assert (await client.get(f"/posts/{post_id}/comments?before=nope")).status_code == 400


async def test_concurrent_comments_count_exactly(client, db, post_id, make_user):
    users = [await make_user() for _ in range(20)]
    results = await asyncio.gather(*(comment(client, post_id, u) for u in users))
    assert all(r.status_code == 201 for r in results)
    ids = [r.json()["comment"]["id"] for r in results]
    await asyncio.gather(*(client.delete(f"/comments/{i}", headers=auth_header(u)) for i, u in zip(ids[:7], users)))
    row = await stored(db, post_id)
    assert row["comment_count"] == row["truth"] == 13
    assert row["comment_version"] == 27


async def test_changes_are_published(client, post_id, make_user):
    from app.events import broker

    user = await make_user()
    sub = broker.subscribe({post_id})
    try:
        added = (await comment(client, post_id, user, "live!")).json()
        event = await asyncio.wait_for(sub.queue.get(), timeout=3)
        assert event["type"] == "comments"
        assert event["comment"]["body"] == "live!" and event["comment_count"] == 1
        await client.delete(f"/comments/{added['comment']['id']}", headers=auth_header(user))
        event = await asyncio.wait_for(sub.queue.get(), timeout=3)
        assert (event["deleted_id"], event["comment_count"]) == (added["comment"]["id"], 0)
    finally:
        broker.unsubscribe(sub)


async def test_sse_streams_comments(live_server, client, post_id, make_user):  # noqa: F811
    async with httpx.AsyncClient(base_url=live_server, timeout=10) as http:
        async with http.stream("GET", f"/stream?posts={post_id}") as resp:
            lines = resp.aiter_lines()
            snapshot = await asyncio.wait_for(read_events(lines, 2), timeout=5)
            assert snapshot[1] == ("comments", {"post_id": post_id, "comment_count": 0, "comment_version": 0})
            await comment(client, post_id, await make_user(), "hello stream")
            [(name, data)] = await asyncio.wait_for(read_events(lines, 1), timeout=5)
            assert name == "comments" and "type" not in data
            assert data["comment"]["body"] == "hello stream" and data["comment_count"] == 1
