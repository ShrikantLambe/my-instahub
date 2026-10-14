from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.config import settings
from app.schemas import Post, User


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.open_pool()
    # Redis is optional until Phase 4: an empty REDIS_URL turns it off.
    app.state.redis = (
        aioredis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None
    )
    yield
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
    p.id, p.caption, p.image_key, p.like_count, p.created_at,
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
    return {"status": status, **checks}


@app.get("/posts", response_model=list[Post])
async def list_posts(limit: int = Query(20, ge=1, le=50)) -> list[Post]:
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
    return [Post.from_row(r) for r in rows]


@app.get("/posts/{post_id}", response_model=Post)
async def get_post(post_id: int) -> Post:
    row = await db.get_pool().fetchrow(
        f"SELECT {POST_COLUMNS} FROM posts p JOIN users u ON u.id = p.author_id WHERE p.id = $1",
        post_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="post not found")
    return Post.from_row(row)


@app.get("/users/{username}", response_model=User)
async def get_user(username: str) -> User:
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
    return User(**dict(row))
