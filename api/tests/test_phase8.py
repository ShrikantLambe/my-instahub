"""Phase 8: profile details (bio, city, website) and a profile photo."""

import uuid

import asyncpg
import pytest

from app.config import settings
from tests.test_phase1 import auth_header, make_profile, s3  # noqa: F401 (fixture)


@pytest.fixture
async def user(client):
    user_id = await make_profile(client, f"pf_{uuid.uuid4().hex[:10]}")
    me = (await client.get("/me", headers=auth_header(user_id))).json()
    return user_id, me["username"]


async def patch(client, user_id, **fields):
    return await client.patch("/me", json=fields, headers=auth_header(user_id))


async def test_edit_details(client, user):
    user_id, username = user
    resp = await patch(client, user_id, bio="  Coffee, code\nand cameras  ", city="  Pune,   India ", website="example.com/me")
    assert resp.status_code == 200, resp.text
    me = resp.json()
    assert me["bio"] == "Coffee, code\nand cameras"
    assert me["city"] == "Pune, India"
    assert me["website"] == "https://example.com/me"

    public = (await client.get(f"/users/{username}")).json()
    assert (public["bio"], public["city"], public["website"]) == (me["bio"], me["city"], me["website"])


async def test_only_sent_fields_change(client, user):
    user_id, _ = user
    await patch(client, user_id, bio="hello", city="Paris", display_name="Pat")
    me = (await patch(client, user_id, city="")).json()  # "" clears just the city
    assert (me["bio"], me["city"], me["display_name"]) == ("hello", None, "Pat")
    me = (await patch(client, user_id)).json()  # nothing sent: nothing changes
    assert me["bio"] == "hello"


@pytest.mark.parametrize(
    "fields",
    [
        {"bio": "x" * 151},
        {"bio": "1\n2\n3\n4\n5\n6"},
        {"city": "c" * 61},
        {"display_name": "d" * 51},
        {"website": "javascript:alert(1)"},
        {"website": "ftp://example.com"},
        {"website": "not a site"},
        {"website": "localhost"},
        {"username": "no spaces"},
        {"username": None},
    ],
)
async def test_rejects_bad_values(client, user, fields):
    assert (await patch(client, user[0], **fields)).status_code == 422


async def test_change_username(client, user):
    user_id, _ = user
    new = f"pf_{uuid.uuid4().hex[:10]}"
    assert (await patch(client, user_id, username=new.upper())).json()["username"] == new
    assert (await client.get(f"/users/{new}")).status_code == 200
    assert (await patch(client, user_id, username="alice")).status_code == 409


async def test_needs_profile(client):
    assert (await patch(client, uuid.uuid4(), bio="hi")).status_code == 404
    assert (await client.patch("/me", json={"bio": "hi"})).status_code == 401


async def test_avatar_upload_and_use(client, user, s3, database):  # noqa: F811
    user_id, username = user
    headers = auth_header(user_id)
    upload = (await client.post("/uploads", json={"content_type": "image/png", "purpose": "avatar"}, headers=headers)).json()
    assert upload["key"].startswith(f"avatars/{user_id}/")

    # Not uploaded yet.
    assert (await patch(client, user_id, avatar_key=upload["key"])).status_code == 400
    s3.put_object(Bucket=settings.s3_bucket, Key=upload["key"], Body=b"png", ContentType="image/png")
    me = (await patch(client, user_id, avatar_key=upload["key"])).json()
    url = f"{settings.image_base_url}/{upload['key']}"
    assert me["avatar_url"] == url
    assert (await client.get(f"/users/{username}")).json()["avatar_url"] == url

    # Posts and comments carry the author's photo.
    conn = await asyncpg.connect(database)
    try:
        post_id = await conn.fetchval(
            "INSERT INTO posts (author_id, image_key) VALUES ($1, 'seed/x.jpg') RETURNING id", user_id
        )
    finally:
        await conn.close()
    assert (await client.get(f"/posts/{post_id}")).json()["author"]["avatar_url"] == url
    await client.post(f"/posts/{post_id}/comments", json={"body": "me"}, headers=headers)
    comment = (await client.get(f"/posts/{post_id}/comments")).json()["comments"][0]
    assert comment["author"]["avatar_url"] == url

    # Removing it.
    assert (await patch(client, user_id, avatar_key=None)).json()["avatar_url"] is None


async def test_avatar_must_be_own_avatar_upload(client, user, s3):  # noqa: F811
    user_id, _ = user
    other = uuid.uuid4()
    post_key = f"posts/{user_id}/2026/10/{'a' * 32}.png"  # a post photo, not an avatar
    theirs = f"avatars/{other}/2026/10/{'b' * 32}.png"
    for key in (post_key, theirs):  # both exist, so only ownership can refuse them
        s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=b"x")
    for key in (post_key, theirs, f"avatars/{user_id}/../../etc/passwd", "seed/x.jpg"):
        assert (await patch(client, user_id, avatar_key=key)).status_code == 400, key


async def test_put_me_keeps_details(client, user):
    """Onboarding's PUT /me only touches username and display name."""
    user_id, username = user
    await patch(client, user_id, bio="stays")
    await client.put("/me", json={"username": username, "display_name": "New"}, headers=auth_header(user_id))
    assert (await client.get("/me", headers=auth_header(user_id))).json()["bio"] == "stays"
