"""Comments: add, delete and page through them, keeping posts.comment_count in step.

The comments table is the source of truth and posts.comment_count is a copy, moved in
the same statement as the insert or delete (like likes). comment_version goes up with
every change so live copies of the count can ignore stale updates.
"""

from datetime import datetime
from typing import NamedTuple
from uuid import UUID

import asyncpg

from app.likes import NoProfile, PostNotFound

COLUMNS = """
    c.id, c.post_id, c.body, c.created_at,
    u.id AS author_id, u.username AS author_username, u.display_name AS author_display_name
"""


class CommentCount(NamedTuple):
    count: int
    version: int


class CommentNotFound(Exception):
    pass


class NotAllowed(Exception):
    """Only the comment's author or the post's author may delete a comment."""


ADD_SQL = f"""
WITH c AS (
  INSERT INTO comments (post_id, author_id, body) VALUES ($1, $2, $3)
  RETURNING *
), p AS (
  UPDATE posts SET comment_count = comment_count + 1, comment_version = comment_version + 1
  WHERE id = $1
  RETURNING comment_count, comment_version
)
SELECT {COLUMNS}, p.comment_count, p.comment_version
FROM c JOIN users u ON u.id = c.author_id, p
"""


async def add(pool: asyncpg.Pool, post_id: int, user_id: UUID, body: str) -> tuple[asyncpg.Record, CommentCount]:
    try:
        row = await pool.fetchrow(ADD_SQL, post_id, user_id, body)
    except asyncpg.ForeignKeyViolationError as exc:
        if exc.constraint_name == "comments_author_id_fkey":
            raise NoProfile(user_id) from None
        raise PostNotFound(post_id) from None
    return row, CommentCount(row["comment_count"], row["comment_version"])


DELETE_SQL = """
WITH d AS (
  DELETE FROM comments WHERE id = $1 RETURNING post_id
)
UPDATE posts SET comment_count = comment_count - 1, comment_version = comment_version + 1
WHERE id = (SELECT post_id FROM d)
RETURNING id, comment_count, comment_version
"""


async def delete(pool: asyncpg.Pool, comment_id: int, user_id: UUID) -> tuple[int, CommentCount]:
    """Delete a comment; returns (post_id, new count)."""
    async with pool.acquire() as conn, conn.transaction():
        # Lock the comment so the permission check and the delete see the same row.
        row = await conn.fetchrow(
            """
            SELECT c.author_id, p.author_id AS post_author_id
            FROM comments c JOIN posts p ON p.id = c.post_id
            WHERE c.id = $1
            FOR UPDATE OF c
            """,
            comment_id,
        )
        if row is None:
            raise CommentNotFound(comment_id)
        if user_id not in (row["author_id"], row["post_author_id"]):
            raise NotAllowed(comment_id)
        res = await conn.fetchrow(DELETE_SQL, comment_id)
    return res["id"], CommentCount(res["comment_count"], res["comment_version"])


async def page(
    pool: asyncpg.Pool, post_id: int, before: tuple[datetime, int] | None, limit: int
) -> tuple[list[asyncpg.Record], bool]:
    """Newest first. Returns (rows, more) where more says an older page exists."""
    after_ts, after_id = before or (None, None)
    rows = await pool.fetch(
        f"""
        SELECT {COLUMNS}
        FROM comments c JOIN users u ON u.id = c.author_id
        WHERE c.post_id = $1
          AND ($2::timestamptz IS NULL OR (c.created_at, c.id) < ($2, $3))
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT $4
        """,
        post_id,
        after_ts,
        after_id,
        limit + 1,
    )
    return rows[:limit], len(rows) > limit


async def counts(pool: asyncpg.Pool, post_ids: list[int]) -> dict[int, CommentCount]:
    rows = await pool.fetch(
        "SELECT id, comment_count, comment_version FROM posts WHERE id = ANY($1::bigint[])", post_ids
    )
    return {r["id"]: CommentCount(r["comment_count"], r["comment_version"]) for r in rows}
