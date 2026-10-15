"""Likes: idempotent writes and the batched "did the viewer like these?" read.

The likes table is the source of truth; posts.like_count is a denormalized copy.
Its primary key (post_id, user_id) makes a second like from the same user a no-op,
so a double-tap or a retried request can never inflate the count. The counter only
moves when a row was actually inserted or deleted, inside the same statement.
"""

from uuid import UUID

import asyncpg

LIKE_SQL = """
WITH ins AS (
  INSERT INTO likes (post_id, user_id) VALUES ($1, $2)
  ON CONFLICT DO NOTHING
  RETURNING 1
)
UPDATE posts SET like_count = like_count + 1
WHERE id = $1 AND EXISTS (SELECT 1 FROM ins)
RETURNING like_count
"""

UNLIKE_SQL = """
WITH del AS (
  DELETE FROM likes WHERE post_id = $1 AND user_id = $2
  RETURNING 1
)
UPDATE posts SET like_count = like_count - 1
WHERE id = $1 AND EXISTS (SELECT 1 FROM del)
RETURNING like_count
"""


class PostNotFound(Exception):
    pass


class NoProfile(Exception):
    """Signed in, but no users row yet (username not chosen)."""


async def _current_count(conn: asyncpg.Connection, post_id: int) -> int:
    count = await conn.fetchval("SELECT like_count FROM posts WHERE id = $1", post_id)
    if count is None:
        raise PostNotFound(post_id)
    return count


async def like(pool: asyncpg.Pool, post_id: int, user_id: UUID) -> int:
    """Like a post; returns the new count. Liking twice changes nothing."""
    async with pool.acquire() as conn:
        try:
            count = await conn.fetchval(LIKE_SQL, post_id, user_id)
        except asyncpg.ForeignKeyViolationError as exc:
            if exc.constraint_name == "likes_user_id_fkey":
                raise NoProfile(user_id) from None
            raise PostNotFound(post_id) from None
        # No row back: this user had already liked it. Report the count, change nothing.
        return count if count is not None else await _current_count(conn, post_id)


async def unlike(pool: asyncpg.Pool, post_id: int, user_id: UUID) -> int:
    """Remove a like; returns the new count. Unliking twice changes nothing."""
    async with pool.acquire() as conn:
        count = await conn.fetchval(UNLIKE_SQL, post_id, user_id)
        return count if count is not None else await _current_count(conn, post_id)


async def liked_post_ids(pool: asyncpg.Pool, user_id: UUID, post_ids: list[int]) -> set[int]:
    """One query for a whole page of posts, never one per post."""
    if not post_ids:
        return set()
    rows = await pool.fetch(
        "SELECT post_id FROM likes WHERE user_id = $1 AND post_id = ANY($2::bigint[])",
        user_id,
        post_ids,
    )
    return {r["post_id"] for r in rows}
