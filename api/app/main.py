import asyncio
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import cache, cursor, db, follows, likes, storage
from app.events import broker
from app.auth import current_user_id, optional_user_id
from app.config import settings
from app.schemas import (
    FeedPage,
    FollowOut,
    LikeCountOut,
    LikeOut,
    Post,
    PostIn,
    Profile,
    ProfileIn,
    UploadIn,
    UploadOut,
    User,
)


def make_redis(url: str) -> aioredis.Redis:
    """Hosted Redis (Upstash) closes idle connections. Without retries the next command
    on a pooled connection fails with "Connection closed by server"; with them it just
    reconnects. health_check_interval also keeps the long-lived pub/sub link fresh."""
    return aioredis.from_url(
        url,
        decode_responses=True,
        health_check_interval=30,
        socket_keepalive=True,
        socket_connect_timeout=5,
        retry=Retry(ExponentialBackoff(cap=1, base=0.05), retries=3),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.open_pool()
    # Redis is optional until Phase 4: an empty REDIS_URL turns it off.
    app.state.redis = make_redis(settings.redis_url) if settings.redis_url else None
    await broker.start(app.state.redis)
    yield
    await broker.stop()
    if app.state.redis is not None:
        await app.state.redis.aclose()
    await db.close_pool()


app = FastAPI(title="instahub API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

POST_COLUMNS = """
    p.id, p.caption, p.image_key, p.image_width, p.image_height,
    p.like_count, p.like_version, p.created_at,
    u.id AS author_id, u.username AS author_username, u.display_name AS author_display_name
"""


@app.get("/health")
async def health() -> dict:
    """Liveness plus a ping to each backing store."""
    checks = {}
    try:
        await db.get_pool().fetchval("SELECT 1")
        checks["postgres"] = "ok"
    except Exception as exc:  # report, don't crash, so the health check stays useful
        checks["postgres"] = f"error: {exc}"
    if app.state.redis is None:
        checks["redis"] = "disabled"
    else:
        try:
            await app.state.redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {exc}"
    status = "ok" if all(v in ("ok", "disabled") for v in checks.values()) else "degraded"
    return {"status": status, **checks, "live_connections": broker.connections}


async def with_viewer_likes(posts: list[Post], viewer_id: UUID | None) -> list[Post]:
    """Fill viewer_has_liked for a page of posts with one batched query."""
    if viewer_id is None or not posts:
        return posts
    liked = await likes.liked_post_ids(db.get_pool(), viewer_id, [p.id for p in posts])
    for p in posts:
        p.viewer_has_liked = p.id in liked
    return posts


@app.get("/posts", response_model=list[Post])
async def list_posts(
    limit: int = Query(20, ge=1, le=50), viewer_id: UUID | None = Depends(optional_user_id)
) -> list[Post]:
    """Latest posts from everyone. Phase 3 replaces this with the follow-based feed."""
    rows = await db.get_pool().fetch(
        f"""
        SELECT {POST_COLUMNS}
        FROM posts p JOIN users u ON u.id = p.author_id
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT $1
        """,
        limit,
    )
    return await with_viewer_likes([Post.from_row(r) for r in rows], viewer_id)


async def fetch_post(post_id: int) -> Post | None:
    row = await db.get_pool().fetchrow(
        f"SELECT {POST_COLUMNS} FROM posts p JOIN users u ON u.id = p.author_id WHERE p.id = $1",
        post_id,
    )
    return Post.from_row(row) if row else None


@app.get("/posts/{post_id}", response_model=Post)
async def get_post(post_id: int, viewer_id: UUID | None = Depends(optional_user_id)) -> Post:
    post = await fetch_post(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="post not found")
    return (await with_viewer_likes([post], viewer_id))[0]


@app.get("/users/{username}", response_model=User)
async def get_user(username: str, viewer_id: UUID | None = Depends(optional_user_id)) -> User:
    row = await db.get_pool().fetchrow(
        """
        SELECT u.id, u.username, u.display_name, u.avatar_key, u.created_at,
               (SELECT count(*) FROM posts WHERE author_id = u.id)        AS post_count,
               (SELECT count(*) FROM follows WHERE followee_id = u.id)    AS follower_count,
               (SELECT count(*) FROM follows WHERE follower_id = u.id)    AS following_count
        FROM users u WHERE u.username = $1
        """,
        username,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    user = User(**dict(row))
    if viewer_id is not None and viewer_id != user.id:
        user.viewer_follows = await follows.is_following(db.get_pool(), viewer_id, user.id)
    return user


@app.get("/users/{username}/posts", response_model=list[Post])
async def list_user_posts(
    username: str,
    limit: int = Query(30, ge=1, le=60),
    viewer_id: UUID | None = Depends(optional_user_id),
) -> list[Post]:
    """A user's posts, newest first: the profile grid."""
    rows = await db.get_pool().fetch(
        f"""
        SELECT {POST_COLUMNS}
        FROM posts p JOIN users u ON u.id = p.author_id
        WHERE u.username = $1
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT $2
        """,
        username,
        limit,
    )
    return await with_viewer_likes([Post.from_row(r) for r in rows], viewer_id)


# --- Signed-in endpoints. The user id always comes from the verified token. ---


@app.get("/me", response_model=Profile)
async def get_me(user_id: UUID = Depends(current_user_id)) -> Profile:
    row = await db.get_pool().fetchrow(
        "SELECT id, username, display_name FROM users WHERE id = $1", user_id
    )
    if row is None:
        # Signed in with Supabase but no profile yet: the client sends them to pick a username.
        raise HTTPException(status_code=404, detail="profile not found")
    return Profile(**dict(row))


@app.put("/me", response_model=Profile)
async def put_me(body: ProfileIn, user_id: UUID = Depends(current_user_id)) -> Profile:
    try:
        row = await db.get_pool().fetchrow(
            """
            INSERT INTO users (id, username, display_name) VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE
              SET username = EXCLUDED.username, display_name = EXCLUDED.display_name
            RETURNING id, username, display_name
            """,
            user_id,
            body.username,
            body.display_name,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="username is taken") from None
    return Profile(**dict(row))


@app.post("/uploads", response_model=UploadOut)
async def create_upload(body: UploadIn, user_id: UUID = Depends(current_user_id)) -> UploadOut:
    """Hand out a short-lived URL the browser PUTs the photo to, straight into the bucket."""
    ext = storage.IMAGE_TYPES[body.content_type]
    now = datetime.now(timezone.utc)
    # Keys are namespaced by user so POST /posts can check the caller owns the upload.
    key = f"posts/{user_id}/{now:%Y/%m}/{uuid.uuid4().hex}.{ext}"
    return UploadOut(
        upload_url=storage.presign_put(key, body.content_type),
        key=key,
        expires_in=settings.upload_url_ttl_seconds,
    )


def _owns_key(user_id: UUID, key: str) -> bool:
    exts = "|".join(storage.IMAGE_TYPES.values())
    pattern = rf"posts/{user_id}/\d{{4}}/\d{{2}}/[0-9a-f]{{32}}\.({exts})"
    return re.fullmatch(pattern, key) is not None


@app.post("/posts", response_model=Post, status_code=201)
async def create_post(body: PostIn, user_id: UUID = Depends(current_user_id)) -> Post:
    if not _owns_key(user_id, body.image_key):
        raise HTTPException(status_code=400, detail="image_key is not one of your uploads")
    if not await storage.object_exists(body.image_key):
        raise HTTPException(status_code=400, detail="upload not found; upload the photo first")
    try:
        post_id = await db.get_pool().fetchval(
            "INSERT INTO posts (author_id, caption, image_key, image_width, image_height) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            user_id,
            body.caption,
            body.image_key,
            body.image_width,
            body.image_height,
        )
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=409, detail="create your profile first") from None
    return await fetch_post(post_id)


@app.post("/posts/{post_id}/like", response_model=LikeOut)
async def like_post(post_id: int, user_id: UUID = Depends(current_user_id)) -> LikeOut:
    """Idempotent: liking an already-liked post returns the current count unchanged."""
    try:
        value, changed = await likes.like(db.get_pool(), post_id, user_id)
    except likes.PostNotFound:
        raise HTTPException(status_code=404, detail="post not found") from None
    except likes.NoProfile:
        raise HTTPException(status_code=409, detail="create your profile first") from None
    if changed:
        await after_like_change(post_id, value)
    return LikeOut(post_id=post_id, liked=True, like_count=value.count, like_version=value.version)


@app.delete("/posts/{post_id}/like", response_model=LikeOut)
async def unlike_post(post_id: int, user_id: UUID = Depends(current_user_id)) -> LikeOut:
    """Idempotent mirror of like: removing a like that isn't there changes nothing."""
    try:
        value, changed = await likes.unlike(db.get_pool(), post_id, user_id)
    except likes.PostNotFound:
        raise HTTPException(status_code=404, detail="post not found") from None
    if changed:
        await after_like_change(post_id, value)
    return LikeOut(post_id=post_id, liked=False, like_count=value.count, like_version=value.version)


@app.post("/users/{username}/follow", response_model=FollowOut)
async def follow_user(username: str, user_id: UUID = Depends(current_user_id)) -> FollowOut:
    """Idempotent: following someone you already follow changes nothing."""
    try:
        count = await follows.follow(db.get_pool(), user_id, username)
    except follows.UserNotFound:
        raise HTTPException(status_code=404, detail="user not found") from None
    except asyncpg.CheckViolationError:
        raise HTTPException(status_code=400, detail="you can't follow yourself") from None
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=409, detail="create your profile first") from None
    return FollowOut(username=username, following=True, follower_count=count)


@app.delete("/users/{username}/follow", response_model=FollowOut)
async def unfollow_user(username: str, user_id: UUID = Depends(current_user_id)) -> FollowOut:
    try:
        count = await follows.unfollow(db.get_pool(), user_id, username)
    except follows.UserNotFound:
        raise HTTPException(status_code=404, detail="user not found") from None
    return FollowOut(username=username, following=False, follower_count=count)


@app.get("/feed", response_model=FeedPage)
async def home_feed(
    before: str | None = Query(None, description="next_cursor from the previous page"),
    limit: int = Query(10, ge=1, le=50),
    user_id: UUID = Depends(current_user_id),
) -> FeedPage:
    """Home feed, fan-out on read: the latest posts from people you follow, plus your own.

    Built at request time from follows + posts. The alternative, fan-out on write,
    pushes each new post id into every follower's Redis list; see design.md.
    """
    try:
        after_ts, after_id = cursor.decode(before) if before else (None, None)
    except cursor.BadCursor:
        raise HTTPException(status_code=400, detail="invalid cursor") from None
    rows = await db.get_pool().fetch(
        f"""
        SELECT {POST_COLUMNS}
        FROM posts p JOIN users u ON u.id = p.author_id
        WHERE (p.author_id = $1
               OR p.author_id IN (SELECT followee_id FROM follows WHERE follower_id = $1))
          AND ($2::timestamptz IS NULL OR (p.created_at, p.id) < ($2, $3))
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT $4
        """,
        user_id,
        after_ts,
        after_id,
        limit + 1,  # one extra row tells us whether another page exists
    )
    page, more = rows[:limit], len(rows) > limit
    posts = await with_viewer_likes([Post.from_row(r) for r in page], user_id)
    next_cursor = cursor.encode(page[-1]["created_at"], page[-1]["id"]) if more else None
    return FeedPage(posts=posts, next_cursor=next_cursor)


# --- Phase 4: cached like counts and live updates -------------------------------


async def after_like_change(post_id: int, value: likes.LikeCount) -> None:
    """Postgres has committed; now refresh the cache and tell everyone watching."""
    await cache.store(app.state.redis, post_id, value)
    await broker.publish({"post_id": post_id, "like_count": value.count, "like_version": value.version})


MAX_WATCHED_POSTS = 100


def parse_ids(raw: str) -> list[int]:
    try:
        ids = [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="ids must be comma-separated integers") from None
    if not 0 < len(ids) <= MAX_WATCHED_POSTS:
        raise HTTPException(status_code=400, detail=f"give 1-{MAX_WATCHED_POSTS} post ids")
    return ids


@app.get("/likes", response_model=list[LikeCountOut])
async def like_counts(ids: str = Query(..., description="comma-separated post ids")) -> list[LikeCountOut]:
    """Current counts for a set of posts, read cache-aside (Redis first, Postgres on a miss)."""
    counts = await cache.get_counts(app.state.redis, db.get_pool(), parse_ids(ids))
    return [LikeCountOut(post_id=pid, like_count=v.count, like_version=v.version) for pid, v in counts.items()]


HEARTBEAT_SECONDS = 15


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/stream")
async def stream(posts: str = Query(..., description="comma-separated post ids to watch")):
    """Server-Sent Events: a snapshot of each watched post's count, then every change live.

    The browser's EventSource reconnects on its own; each reconnect starts with a fresh
    snapshot, so anything missed while disconnected is caught up. Counts carry
    like_version and clients ignore anything older than what they already show.
    """
    post_ids = parse_ids(posts)
    sub = broker.subscribe(set(post_ids))  # subscribe before the snapshot: nothing slips between

    async def events():
        try:
            yield "retry: 3000\n\n"
            snapshot = await cache.get_counts(app.state.redis, db.get_pool(), post_ids)
            for pid, v in snapshot.items():
                yield sse("likes", {"post_id": pid, "like_count": v.count, "like_version": v.version})
            while True:
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_SECONDS)
                    yield sse("likes", event)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keeps proxies from closing an idle connection
        finally:
            broker.unsubscribe(sub)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
