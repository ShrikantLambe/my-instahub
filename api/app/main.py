import asyncio
import contextlib
import json
import logging
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

from app import cache, comments, counter, cursor, db, follows, likes, notifications, storage
from app.events import broker
from app.auth import current_user_id, optional_user_id
from app.config import settings
from app.schemas import (
    Comment,
    CommentChange,
    CommentIn,
    CommentPage,
    FeedPage,
    FollowOut,
    LikeCountOut,
    LikeOut,
    Notification,
    NotificationPage,
    Post,
    PostIn,
    Profile,
    ProfileIn,
    ProfileUpdate,
    StreamTicket,
    UnreadOut,
    UploadIn,
    UploadOut,
    User,
    avatar_url,
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


log = logging.getLogger("app")


def buffered() -> bool:
    """Buffered counter in effect (it needs Redis; without it we stay direct)."""
    return settings.counter_mode == "buffered" and getattr(app.state, "redis", None) is not None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.open_pool()
    # Redis is optional until Phase 4: an empty REDIS_URL turns it off.
    app.state.redis = make_redis(settings.redis_url) if settings.redis_url else None
    await broker.start(app.state.redis)
    background = None
    if buffered():
        background = asyncio.create_task(
            counter.run_background(
                app.state.redis,
                db.get_pool(),
                settings.flush_interval_seconds,
                settings.reconcile_interval_seconds,
            )
        )
    elif settings.counter_mode == "buffered":
        log.warning("COUNTER_MODE=buffered needs REDIS_URL; using direct mode")
    yield
    if background is not None:
        background.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await background
        await counter.flush(app.state.redis, db.get_pool())  # don't strand pending deltas
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
    p.like_count, p.like_version, p.comment_count, p.comment_version, p.created_at,
    u.id AS author_id, u.username AS author_username, u.display_name AS author_display_name,
    u.avatar_key AS author_avatar_key
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
    return {
        "status": status,
        **checks,
        "counter_mode": "buffered" if buffered() else "direct",
        "live_connections": broker.connections,
    }


async def with_viewer_likes(posts: list[Post], viewer_id: UUID | None) -> list[Post]:
    """Fill viewer_has_liked for a page of posts with one batched query.

    In buffered mode posts.like_count lags by up to one flush, so the live counts come
    from Redis instead (one MGET for the page)."""
    if buffered() and posts:
        live = await counter.get_counts(app.state.redis, db.get_pool(), [p.id for p in posts])
        for p in posts:
            if p.id in live:
                p.like_count, p.like_version = live[p.id].count, live[p.id].version
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
        SELECT u.id, u.username, u.display_name, u.avatar_key, u.bio, u.city, u.website, u.created_at,
               (SELECT count(*) FROM posts WHERE author_id = u.id)        AS post_count,
               (SELECT count(*) FROM follows WHERE followee_id = u.id)    AS follower_count,
               (SELECT count(*) FROM follows WHERE follower_id = u.id)    AS following_count
        FROM users u WHERE u.username = $1
        """,
        username,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    user = User(**dict(row), avatar_url=avatar_url(row["avatar_key"]))
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


PROFILE_COLUMNS = "id, username, display_name, avatar_key, bio, city, website"


@app.get("/me", response_model=Profile)
async def get_me(user_id: UUID = Depends(current_user_id)) -> Profile:
    row = await db.get_pool().fetchrow(f"SELECT {PROFILE_COLUMNS} FROM users WHERE id = $1", user_id)
    if row is None:
        # Signed in with Supabase but no profile yet: the client sends them to pick a username.
        raise HTTPException(status_code=404, detail="profile not found")
    return Profile.from_row(row)


@app.put("/me", response_model=Profile)
async def put_me(body: ProfileIn, user_id: UUID = Depends(current_user_id)) -> Profile:
    """Create the profile (onboarding). Other details are edited with PATCH /me."""
    try:
        row = await db.get_pool().fetchrow(
            f"""
            INSERT INTO users (id, username, display_name) VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE
              SET username = EXCLUDED.username, display_name = EXCLUDED.display_name
            RETURNING {PROFILE_COLUMNS}
            """,
            user_id,
            body.username,
            body.display_name,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="username is taken") from None
    return Profile.from_row(row)


@app.patch("/me", response_model=Profile)
async def patch_me(body: ProfileUpdate, user_id: UUID = Depends(current_user_id)) -> Profile:
    """Edit profile: username, display name, bio, city, website and photo.

    Only the fields present in the request change, so the form can send just what was
    edited. A new photo must be one of this user's avatar uploads that has landed.
    """
    changes = body.model_dump(include=body.model_fields_set)
    key = changes.get("avatar_key")
    if key is not None:
        if not _owns_key(user_id, key, "avatars"):
            raise HTTPException(status_code=400, detail="avatar_key is not one of your uploads")
        if not await storage.object_exists(key):
            raise HTTPException(status_code=400, detail="upload not found; upload the photo first")
    if not changes:
        return await get_me(user_id)
    # Column names come from the model's fixed field list, never from the request.
    sets = ", ".join(f"{col} = ${i}" for i, col in enumerate(changes, start=2))
    try:
        row = await db.get_pool().fetchrow(
            f"UPDATE users SET {sets} WHERE id = $1 RETURNING {PROFILE_COLUMNS}", user_id, *changes.values()
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="username is taken") from None
    if row is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return Profile.from_row(row)


@app.post("/uploads", response_model=UploadOut)
async def create_upload(body: UploadIn, user_id: UUID = Depends(current_user_id)) -> UploadOut:
    """Hand out a short-lived URL the browser PUTs the photo to, straight into the bucket."""
    ext = storage.IMAGE_TYPES[body.content_type]
    now = datetime.now(timezone.utc)
    # Keys are namespaced by purpose and user so POST /posts and PATCH /me can check
    # the caller owns the upload (and that a post photo isn't used as an avatar).
    folder = "avatars" if body.purpose == "avatar" else "posts"
    key = f"{folder}/{user_id}/{now:%Y/%m}/{uuid.uuid4().hex}.{ext}"
    return UploadOut(
        upload_url=storage.presign_put(key, body.content_type),
        key=key,
        expires_in=settings.upload_url_ttl_seconds,
    )


def _owns_key(user_id: UUID, key: str, folder: str = "posts") -> bool:
    exts = "|".join(storage.IMAGE_TYPES.values())
    pattern = rf"{folder}/{user_id}/\d{{4}}/\d{{2}}/[0-9a-f]{{32}}\.({exts})"
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
        value, changed = await (
            buffered_change(post_id, user_id, +1) if buffered() else likes.like(db.get_pool(), post_id, user_id)
        )
    except likes.PostNotFound:
        raise HTTPException(status_code=404, detail="post not found") from None
    except likes.NoProfile:
        raise HTTPException(status_code=409, detail="create your profile first") from None
    if changed:
        await after_like_change(post_id, value)
        await notify(notifications.on_like(db.get_pool(), post_id, user_id))
    return LikeOut(post_id=post_id, liked=True, like_count=value.count, like_version=value.version)


@app.delete("/posts/{post_id}/like", response_model=LikeOut)
async def unlike_post(post_id: int, user_id: UUID = Depends(current_user_id)) -> LikeOut:
    """Idempotent mirror of like: removing a like that isn't there changes nothing."""
    try:
        value, changed = await (
            buffered_change(post_id, user_id, -1) if buffered() else likes.unlike(db.get_pool(), post_id, user_id)
        )
    except likes.PostNotFound:
        raise HTTPException(status_code=404, detail="post not found") from None
    if changed:
        await after_like_change(post_id, value)
        await notify(notifications.on_unlike(db.get_pool(), post_id, user_id))
    return LikeOut(post_id=post_id, liked=False, like_count=value.count, like_version=value.version)


@app.post("/users/{username}/follow", response_model=FollowOut)
async def follow_user(username: str, user_id: UUID = Depends(current_user_id)) -> FollowOut:
    """Idempotent: following someone you already follow changes nothing."""
    try:
        result = await follows.follow(db.get_pool(), user_id, username)
    except follows.UserNotFound:
        raise HTTPException(status_code=404, detail="user not found") from None
    except asyncpg.CheckViolationError:
        raise HTTPException(status_code=400, detail="you can't follow yourself") from None
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=409, detail="create your profile first") from None
    if result.changed:
        await notify(notifications.on_follow(db.get_pool(), result.followee, user_id))
    return FollowOut(username=username, following=True, follower_count=result.follower_count)


@app.delete("/users/{username}/follow", response_model=FollowOut)
async def unfollow_user(username: str, user_id: UUID = Depends(current_user_id)) -> FollowOut:
    try:
        result = await follows.unfollow(db.get_pool(), user_id, username)
    except follows.UserNotFound:
        raise HTTPException(status_code=404, detail="user not found") from None
    if result.changed:
        await notify(notifications.on_unfollow(db.get_pool(), result.followee, user_id))
    return FollowOut(username=username, following=False, follower_count=result.follower_count)


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


# --- Comments ---------------------------------------------------------------------


@app.get("/posts/{post_id}/comments", response_model=CommentPage)
async def list_comments(
    post_id: int,
    before: str | None = Query(None, description="next_cursor from the previous page"),
    limit: int = Query(20, ge=1, le=50),
) -> CommentPage:
    """A post's comments, newest first, paged by cursor like the feed."""
    try:
        after = cursor.decode(before) if before else None
    except cursor.BadCursor:
        raise HTTPException(status_code=400, detail="invalid cursor") from None
    pool = db.get_pool()
    count = (await comments.counts(pool, [post_id])).get(post_id)
    if count is None:
        raise HTTPException(status_code=404, detail="post not found")
    rows, more = await comments.page(pool, post_id, after, limit)
    return CommentPage(
        comments=[Comment.from_row(r) for r in rows],
        next_cursor=cursor.encode(rows[-1]["created_at"], rows[-1]["id"]) if more else None,
        comment_count=count.count,
        comment_version=count.version,
    )


@app.post("/posts/{post_id}/comments", response_model=CommentChange, status_code=201)
async def add_comment(post_id: int, body: CommentIn, user_id: UUID = Depends(current_user_id)) -> CommentChange:
    try:
        row, count = await comments.add(db.get_pool(), post_id, user_id, body.body)
    except likes.PostNotFound:
        raise HTTPException(status_code=404, detail="post not found") from None
    except likes.NoProfile:
        raise HTTPException(status_code=409, detail="create your profile first") from None
    change = CommentChange(
        post_id=post_id, comment=Comment.from_row(row), comment_count=count.count, comment_version=count.version
    )
    await broker.publish({"type": "comments", **change.model_dump(mode="json")})
    await notify(notifications.on_comment(db.get_pool(), post_id, row["id"], user_id))
    return change


@app.delete("/comments/{comment_id}", response_model=CommentChange)
async def delete_comment(comment_id: int, user_id: UUID = Depends(current_user_id)) -> CommentChange:
    """The comment's author can delete it, and so can the author of the post it's on."""
    try:
        deleted = await comments.delete(db.get_pool(), comment_id, user_id)
    except comments.CommentNotFound:
        raise HTTPException(status_code=404, detail="comment not found") from None
    except comments.NotAllowed:
        raise HTTPException(status_code=403, detail="you can only delete your own comments") from None
    change = CommentChange(
        post_id=deleted.post_id,
        deleted_id=comment_id,
        comment_count=deleted.count.count,
        comment_version=deleted.count.version,
    )
    await broker.publish({"type": "comments", **change.model_dump(mode="json")})
    await refresh_badge(deleted.post_author)  # its notification, if any, went with it
    return change


# --- Phase 4: cached like counts and live updates -------------------------------


async def after_like_change(post_id: int, value: likes.LikeCount) -> None:
    """Postgres has committed; now refresh the cache and tell everyone watching."""
    if not buffered():  # buffered mode's live counters in Redis already are the cache
        await cache.store(app.state.redis, post_id, value)
    await broker.publish({"post_id": post_id, "like_count": value.count, "like_version": value.version})


async def buffered_change(post_id: int, user_id: UUID, delta: int) -> tuple[likes.LikeCount, bool]:
    """Phase 6 write path: write only the likes row, count in Redis, flush later."""
    pool, r = db.get_pool(), app.state.redis
    changed = await (likes.insert_like if delta > 0 else likes.delete_like)(pool, post_id, user_id)
    if not changed:
        return await counter.current(r, pool, post_id), False
    return await counter.bump(r, pool, post_id, delta), True


async def counts_for(post_ids: list[int]) -> dict[int, likes.LikeCount]:
    if buffered():
        return await counter.get_counts(app.state.redis, db.get_pool(), post_ids)
    return await cache.get_counts(app.state.redis, db.get_pool(), post_ids)


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
    counts = await counts_for(parse_ids(ids))
    return [LikeCountOut(post_id=pid, like_count=v.count, like_version=v.version) for pid, v in counts.items()]


HEARTBEAT_SECONDS = 15


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/stream")
async def stream(posts: str = Query(..., description="comma-separated post ids to watch")):
    """Server-Sent Events: a snapshot of each watched post's counts, then every change live.

    Events: `likes` (like_count, like_version) and `comments` (comment_count,
    comment_version, plus the new `comment` or the `deleted_id` when one changed).

    The browser's EventSource reconnects on its own; each reconnect starts with a fresh
    snapshot, so anything missed while disconnected is caught up. Counts carry
    like_version and clients ignore anything older than what they already show.
    """
    post_ids = parse_ids(posts)
    sub = broker.subscribe(set(post_ids))  # subscribe before the snapshot: nothing slips between

    async def events():
        try:
            yield "retry: 3000\n\n"
            snapshot = await counts_for(post_ids)
            for pid, v in snapshot.items():
                yield sse("likes", {"post_id": pid, "like_count": v.count, "like_version": v.version})
            for pid, c in (await comments.counts(db.get_pool(), post_ids)).items():
                yield sse("comments", {"post_id": pid, "comment_count": c.count, "comment_version": c.version})
            while True:
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_SECONDS)
                    # Like events predate the type field; everything else names its type.
                    kind = event.get("type", "likes")
                    yield sse(kind, {k: v for k, v in event.items() if k != "type"})
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keeps proxies from closing an idle connection
        finally:
            broker.unsubscribe(sub)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Notifications -----------------------------------------------------------------


async def _push_unread(recipient: UUID, row: asyncpg.Record | None = None) -> None:
    """Tell the recipient's open tabs their new unread count (and the new notification)."""
    event = {
        "type": "notification",
        "user_id": str(recipient),
        "unread_count": await notifications.unread_count(db.get_pool(), recipient),
    }
    if row is not None:
        event["notification"] = Notification.from_row(row).model_dump(mode="json")
    await broker.publish(event)


async def notify(action) -> None:
    """Run a notifications.on_* call after the action committed. Best effort: a failure
    here is logged, never turned into an error for the like, comment or follow."""
    try:
        result = await action
        if result is None:
            return
        if isinstance(result, tuple):  # (recipient, row): a new notification
            await _push_unread(*result)
        else:  # recipient whose notification was withdrawn
            await _push_unread(result)
    except Exception:
        log.warning("notification side effect failed", exc_info=True)


async def refresh_badge(recipient: UUID) -> None:
    """Push a recipient's current unread count (after reads or cascaded deletes)."""
    try:
        await _push_unread(recipient)
    except Exception:
        log.warning("unread count push failed", exc_info=True)


@app.get("/notifications", response_model=NotificationPage)
async def list_notifications(
    before: str | None = Query(None, description="next_cursor from the previous page"),
    limit: int = Query(20, ge=1, le=50),
    user_id: UUID = Depends(current_user_id),
) -> NotificationPage:
    try:
        after = cursor.decode(before) if before else None
    except cursor.BadCursor:
        raise HTTPException(status_code=400, detail="invalid cursor") from None
    pool = db.get_pool()
    rows, more = await notifications.page(pool, user_id, after, limit)
    return NotificationPage(
        notifications=[Notification.from_row(r) for r in rows],
        next_cursor=cursor.encode(rows[-1]["created_at"], rows[-1]["id"]) if more else None,
        unread_count=await notifications.unread_count(pool, user_id),
    )


@app.get("/notifications/unread", response_model=UnreadOut)
async def unread_notifications(user_id: UUID = Depends(current_user_id)) -> UnreadOut:
    return UnreadOut(unread_count=await notifications.unread_count(db.get_pool(), user_id))


@app.post("/notifications/read", response_model=UnreadOut)
async def read_notifications(user_id: UUID = Depends(current_user_id)) -> UnreadOut:
    """Mark everything read (opening the activity page does this, like Instagram)."""
    await notifications.mark_all_read(db.get_pool(), user_id)
    await refresh_badge(user_id)  # other open tabs clear their badge too
    return UnreadOut(unread_count=0)


@app.post("/notifications/ticket", response_model=StreamTicket)
async def notification_ticket(user_id: UUID = Depends(current_user_id)) -> StreamTicket:
    """EventSource can't send an Authorization header, so the browser trades its token
    for a short-lived, single-use ticket and opens the stream with that."""
    ticket = await notifications.issue_ticket(app.state.redis, user_id)
    return StreamTicket(ticket=ticket, expires_in=notifications.TICKET_TTL)


@app.get("/notifications/stream")
async def notification_stream(ticket: str = Query(..., description="from POST /notifications/ticket")):
    """Server-Sent Events for one user: `unread` (the current count) first, then a
    `notification` event (unread_count plus the new notification, if any) per change.

    The ticket is used up by opening the stream, so after a disconnect the browser gets a
    fresh ticket and reconnects; the opening `unread` event catches it up.
    """
    user_id = await notifications.redeem_ticket(app.state.redis, ticket)
    if user_id is None:
        raise HTTPException(status_code=401, detail="ticket expired or already used")
    sub = broker.subscribe(set(), user_id=str(user_id))

    async def events():
        try:
            yield "retry: 3000\n\n"
            yield sse("unread", {"unread_count": await notifications.unread_count(db.get_pool(), user_id)})
            while True:
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_SECONDS)
                    yield sse("notification", {k: v for k, v in event.items() if k not in ("type", "user_id")})
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            broker.unsubscribe(sub)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
