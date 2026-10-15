"""Phase 1: auth, profiles, presigned uploads, creating posts."""

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from moto import mock_aws

from app import auth, storage
from app.config import settings


def token_for(user_id, secret="test-secret-at-least-32-bytes-long!!", **overrides) -> str:
    claims = {"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 3600}
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


def auth_header(user_id, **kw) -> dict:
    return {"Authorization": f"Bearer {token_for(user_id, **kw)}"}


@pytest.fixture
def s3():
    with mock_aws():
        storage.client.cache_clear()
        storage.client().create_bucket(Bucket=settings.s3_bucket)
        yield storage.client()
    storage.client.cache_clear()


async def make_profile(client, username) -> uuid.UUID:
    user_id = uuid.uuid4()
    resp = await client.put("/me", json={"username": username}, headers=auth_header(user_id))
    assert resp.status_code == 200, resp.text
    return user_id


# --- auth ---


async def test_me_requires_token(client):
    assert (await client.get("/me")).status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        token_for(uuid.uuid4(), secret="wrong-secret"),
        token_for(uuid.uuid4(), aud="anon"),
        token_for(uuid.uuid4(), exp=int(time.time()) - 10),
        token_for("not-a-uuid"),
        jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated"}, None, algorithm="none"),
    ],
    ids=["garbage", "bad-signature", "wrong-audience", "expired", "bad-sub", "alg-none"],
)
async def test_bad_tokens_rejected(client, token):
    resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_asymmetric_token_via_jwks(client, monkeypatch):
    """New Supabase projects sign with ES256 keys published at a JWKS endpoint."""
    private_key = ec.generate_private_key(ec.SECP256R1())

    class FakeJWKS:
        def get_signing_key_from_jwt(self, token):
            return type("Key", (), {"key": private_key.public_key()})()

    monkeypatch.setattr(auth, "_jwks_client", FakeJWKS())
    user_id = uuid.uuid4()
    token = jwt.encode(
        {"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 60},
        private_key,
        algorithm="ES256",
        headers={"kid": "test"},
    )
    resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404  # authenticated; just no profile yet


# --- profile ---


async def test_profile_lifecycle(client):
    user_id = uuid.uuid4()
    headers = auth_header(user_id)
    assert (await client.get("/me", headers=headers)).status_code == 404

    resp = await client.put("/me", json={"username": "Dana_1", "display_name": "Dana"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"id": str(user_id), "username": "dana_1", "display_name": "Dana"}

    assert (await client.get("/me", headers=headers)).json()["username"] == "dana_1"
    assert (await client.get("/users/dana_1")).status_code == 200


async def test_username_taken_and_invalid(client):
    headers = auth_header(uuid.uuid4())
    assert (await client.put("/me", json={"username": "alice"}, headers=headers)).status_code == 409
    for bad in ["ab", "has space", "x" * 31, "emoji😀"]:
        resp = await client.put("/me", json={"username": bad}, headers=headers)
        assert resp.status_code == 422, bad


# --- uploads and posts ---


async def test_upload_url_is_presigned_and_namespaced(client, s3):
    user_id = uuid.uuid4()
    resp = await client.post("/uploads", json={"content_type": "image/png"}, headers=auth_header(user_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"].startswith(f"posts/{user_id}/") and body["key"].endswith(".png")
    assert "X-Amz-Signature=" in body["upload_url"]
    assert body["expires_in"] == settings.upload_url_ttl_seconds


async def test_upload_rejects_non_images(client):
    resp = await client.post("/uploads", json={"content_type": "text/html"}, headers=auth_header(uuid.uuid4()))
    assert resp.status_code == 422


async def test_create_post_end_to_end(client, s3):
    user_id = await make_profile(client, "erin")
    headers = auth_header(user_id)
    key = (await client.post("/uploads", json={"content_type": "image/jpeg"}, headers=headers)).json()["key"]
    s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=b"\xff\xd8fake-jpeg", ContentType="image/jpeg")

    resp = await client.post("/posts", json={"image_key": key, "caption": "hello"}, headers=headers)
    assert resp.status_code == 201, resp.text
    post = resp.json()
    assert post["author"]["username"] == "erin"
    assert post["image_url"] == f"https://cdn.example/instahub/{key}"
    assert post["like_count"] == 0

    grid = (await client.get("/users/erin/posts")).json()
    assert [p["id"] for p in grid] == [post["id"]]


async def test_create_post_rejects_someone_elses_key(client, s3):
    owner = uuid.uuid4()
    key = (await client.post("/uploads", json={"content_type": "image/jpeg"}, headers=auth_header(owner))).json()["key"]
    s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=b"x")
    thief = await make_profile(client, "mallory")
    resp = await client.post("/posts", json={"image_key": key}, headers=auth_header(thief))
    assert resp.status_code == 400


async def test_create_post_requires_uploaded_object(client, s3):
    user_id = await make_profile(client, "frank")
    headers = auth_header(user_id)
    key = (await client.post("/uploads", json={"content_type": "image/jpeg"}, headers=headers)).json()["key"]
    resp = await client.post("/posts", json={"image_key": key}, headers=headers)
    assert resp.status_code == 400
    assert "upload not found" in resp.json()["detail"]


async def test_create_post_requires_profile(client, s3):
    user_id = uuid.uuid4()
    headers = auth_header(user_id)
    key = (await client.post("/uploads", json={"content_type": "image/webp"}, headers=headers)).json()["key"]
    s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=b"x")
    resp = await client.post("/posts", json={"image_key": key}, headers=headers)
    assert resp.status_code == 409


async def test_post_requires_auth(client):
    assert (await client.post("/posts", json={"image_key": "x"})).status_code == 401


@pytest.mark.parametrize(
    "raw",
    [
        "https://abc.supabase.co",
        "https://abc.supabase.co/",
        "https://abc.supabase.co/rest/v1/",
        "  https://abc.supabase.co/auth/v1 ",
    ],
)
def test_supabase_url_keeps_only_origin(raw):
    from app.config import Settings

    assert Settings(supabase_url=raw).supabase_url == "https://abc.supabase.co"
