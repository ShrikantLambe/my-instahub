"""Phase 9: notifications for likes, comments and follows; unread badge; live stream."""

import asyncio
import uuid

import asyncpg
import httpx
import pytest

from app import notifications
from app.config import settings
from tests.test_phase1 import auth_header
from tests.test_phase4 import live_server, read_events  # noqa: F401 (fixture)


@pytest.fixture
async def make_user(client):
    async def _make():
        user_id = uuid.uuid4()
        name = f"nt_{user_id.hex[:10]}"
        resp = await client.put("/me", json={"username": name}, headers=auth_header(user_id))
        assert resp.status_code == 200, resp.text
        return user_id, name

    return _make


@pytest.fixture
async def author(make_user):
    return await make_user()


@pytest.fixture
async def post_id(database, author):
    conn = await asyncpg.connect(database)
    try:
        return await conn.fetchval(
            "INSERT INTO posts (author_id, image_key) VALUES ($1, 'seed/n.jpg') RETURNING id", author[0]
        )
    finally:
        await conn.close()


async def inbox(client, user_id):
    resp = await client.get("/notifications", headers=auth_header(user_id))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_like_notifies_author_once_and_unlike_withdraws(client, author, post_id, make_user):
    liker, liker_name = await make_user()
    h = auth_header(liker)
    await client.post(f"/posts/{post_id}/like", headers=h)
    await client.post(f"/posts/{post_id}/like", headers=h)  # repeat: no second notification
    box = await inbox(client, author[0])
    assert box["unread_count"] == 1
    [n] = box["notifications"]
    assert (n["kind"], n["actor"]["username"], n["post"]["id"], n["read"]) == ("like", liker_name, post_id, False)
    assert n["post"]["image_url"].endswith("seed/n.jpg") and n["comment"] is None

    await client.delete(f"/posts/{post_id}/like", headers=h)
    assert (await inbox(client, author[0]))["unread_count"] == 0
    await client.post(f"/posts/{post_id}/like", headers=h)  # like again: notified again
    assert (await inbox(client, author[0]))["unread_count"] == 1


async def test_no_notification_for_your_own_actions(client, author, post_id):
    h = auth_header(author[0])
    await client.post(f"/posts/{post_id}/like", headers=h)
    await client.post(f"/posts/{post_id}/comments", json={"body": "me again"}, headers=h)
    assert (await inbox(client, author[0]))["notifications"] == []


async def test_comment_notification_goes_with_the_comment(client, author, post_id, make_user):
    commenter, _ = await make_user()
    added = await client.post(f"/posts/{post_id}/comments", json={"body": "great shot"}, headers=auth_header(commenter))
    [n] = (await inbox(client, author[0]))["notifications"]
    assert (n["kind"], n["comment"]["body"]) == ("comment", "great shot")

    # The post's author deletes it: the notification goes too.
    await client.delete(f"/comments/{added.json()['comment']['id']}", headers=auth_header(author[0]))
    assert (await inbox(client, author[0]))["notifications"] == []


async def test_follow_and_unfollow(client, author, make_user):
    fan, fan_name = await make_user()
    h = auth_header(fan)
    await client.post(f"/users/{author[1]}/follow", headers=h)
    await client.post(f"/users/{author[1]}/follow", headers=h)  # repeat
    [n] = (await inbox(client, author[0]))["notifications"]
    assert (n["kind"], n["actor"]["username"], n["post"]) == ("follow", fan_name, None)
    await client.delete(f"/users/{author[1]}/follow", headers=h)
    assert (await inbox(client, author[0]))["notifications"] == []


async def test_mark_read(client, author, post_id, make_user):
    for _ in range(3):
        await client.post(f"/posts/{post_id}/like", headers=auth_header((await make_user())[0]))
    assert (await client.get("/notifications/unread", headers=auth_header(author[0]))).json() == {"unread_count": 3}
    assert (await client.post("/notifications/read", headers=auth_header(author[0]))).json() == {"unread_count": 0}
    box = await inbox(client, author[0])
    assert box["unread_count"] == 0 and all(n["read"] for n in box["notifications"])
    await client.post(f"/posts/{post_id}/like", headers=auth_header((await make_user())[0]))
    box = await inbox(client, author[0])
    assert box["unread_count"] == 1 and [n["read"] for n in box["notifications"]] == [False, True, True, True]


async def test_paging_and_privacy(client, author, post_id, make_user):
    for _ in range(25):
        await client.post(f"/posts/{post_id}/like", headers=auth_header((await make_user())[0]))
    seen, cursor = [], None
    while True:
        qs = "?limit=10" + (f"&before={cursor}" if cursor else "")
        page = (await client.get(f"/notifications{qs}", headers=auth_header(author[0]))).json()
        seen += [n["id"] for n in page["notifications"]]
        if not (cursor := page["next_cursor"]):
            break
    assert len(seen) == len(set(seen)) == 25 and seen == sorted(seen, reverse=True)
    stranger, _ = await make_user()
    assert (await inbox(client, stranger))["notifications"] == []
    assert (await client.get("/notifications")).status_code == 401
    assert (await client.get("/notifications?before=bad", headers=auth_header(author[0]))).status_code == 400


async def test_buffered_mode_notifies_too(client, author, post_id, make_user, monkeypatch):
    monkeypatch.setattr(settings, "counter_mode", "buffered")
    await client.post(f"/posts/{post_id}/like", headers=auth_header((await make_user())[0]))
    assert (await inbox(client, author[0]))["unread_count"] == 1


async def test_notification_failure_does_not_fail_the_like(client, author, post_id, make_user, monkeypatch):
    async def boom(*a):
        raise RuntimeError("notifications table is on fire")

    monkeypatch.setattr(notifications, "on_like", boom)
    resp = await client.post(f"/posts/{post_id}/like", headers=auth_header((await make_user())[0]))
    assert resp.status_code == 200 and resp.json()["like_count"] == 1


async def test_events_are_addressed_to_the_recipient(client, author, post_id, make_user):
    from app.events import broker

    mine = broker.subscribe(set(), user_id=str(author[0]))
    watcher = broker.subscribe({post_id})  # someone watching the post's counts
    try:
        liker, liker_name = await make_user()
        await client.post(f"/posts/{post_id}/like", headers=auth_header(liker))
        event = await asyncio.wait_for(mine.queue.get(), timeout=3)
        assert event["type"] == "notification" and event["unread_count"] == 1
        assert event["notification"]["actor"]["username"] == liker_name
        assert mine.queue.empty()  # the like-count event isn't for this subscription
        seen = [await asyncio.wait_for(watcher.queue.get(), timeout=3)]
        await asyncio.sleep(0.2)
        while not watcher.queue.empty():
            seen.append(watcher.queue.get_nowait())
        assert all(e.get("type") != "notification" for e in seen)  # nor this one for them
    finally:
        broker.unsubscribe(mine)
        broker.unsubscribe(watcher)


async def test_tickets_are_single_use(client, author):
    resp = await client.post("/notifications/ticket", headers=auth_header(author[0]))
    ticket = resp.json()["ticket"]
    assert resp.json()["expires_in"] == notifications.TICKET_TTL
    from app.main import app

    assert await notifications.redeem_ticket(app.state.redis, ticket) == author[0]
    assert await notifications.redeem_ticket(app.state.redis, ticket) is None
    assert (await client.get(f"/notifications/stream?ticket={ticket}")).status_code == 401
    assert (await client.get("/notifications/stream?ticket=made-up")).status_code == 401
    assert (await client.post("/notifications/ticket")).status_code == 401


async def test_local_tickets_without_redis(author):
    ticket = await notifications.issue_ticket(None, author[0])
    assert await notifications.redeem_ticket(None, ticket) == author[0]
    assert await notifications.redeem_ticket(None, ticket) is None


async def test_stream_delivers_live(live_server, client, author, post_id, make_user):  # noqa: F811
    # The ticket is issued by this process and redeemed by the other one (shared Redis).
    ticket = (await client.post("/notifications/ticket", headers=auth_header(author[0]))).json()["ticket"]
    async with httpx.AsyncClient(base_url=live_server, timeout=10) as http:
        async with http.stream("GET", f"/notifications/stream?ticket={ticket}") as resp:
            assert resp.status_code == 200
            lines = resp.aiter_lines()
            assert await asyncio.wait_for(read_events(lines, 1), timeout=5) == [("unread", {"unread_count": 0})]
            commenter, name = await make_user()
            await client.post(f"/posts/{post_id}/comments", json={"body": "wow"}, headers=auth_header(commenter))
            [(kind, data)] = await asyncio.wait_for(read_events(lines, 1), timeout=5)
            assert kind == "notification" and data["unread_count"] == 1
            assert data["notification"]["comment"]["body"] == "wow" and "user_id" not in data
