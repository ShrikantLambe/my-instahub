"""Notifications: "bob liked your post", "carol commented: …", "dan followed you".

They are a side effect written after the action itself has committed, best effort: if
writing one fails, the like or comment still stands. Undoing a like or a follow removes
its notification again, and a deleted comment takes its notification with it (FK
cascade). Nobody is notified about their own actions.

Live delivery goes through the same Redis channel as post events, addressed to the
recipient's user id. Browsers can't put an Authorization header on an EventSource, so
the stream is opened with a short-lived single-use ticket fetched with the token.
"""

import secrets
import time
from datetime import datetime
from uuid import UUID

import asyncpg
import redis.asyncio as aioredis

COLUMNS = """
    n.id, n.kind, n.created_at, n.read_at, n.post_id, n.comment_id,
    u.id AS author_id, u.username AS author_username, u.display_name AS author_display_name,
    u.avatar_key AS author_avatar_key,
    p.image_key AS post_image_key,
    c.body AS comment_body
"""

FROM = """
    FROM notifications n
    JOIN users u ON u.id = n.actor_id
    LEFT JOIN posts p ON p.id = n.post_id
    LEFT JOIN comments c ON c.id = n.comment_id
"""


async def _fetch_one(conn, notification_id: int) -> asyncpg.Record:
    return await conn.fetchrow(f"SELECT {COLUMNS} {FROM} WHERE n.id = $1", notification_id)


async def on_like(pool: asyncpg.Pool, post_id: int, actor: UUID) -> tuple[UUID, asyncpg.Record] | None:
    """Notify the post's author. Returns (recipient, row), or None if nothing was added."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO notifications (recipient_id, actor_id, kind, post_id)
            SELECT author_id, $2, 'like', id FROM posts WHERE id = $1 AND author_id <> $2
            ON CONFLICT DO NOTHING
            RETURNING id, recipient_id
            """,
            post_id,
            actor,
        )
        return (row["recipient_id"], await _fetch_one(conn, row["id"])) if row else None


async def on_unlike(pool: asyncpg.Pool, post_id: int, actor: UUID) -> UUID | None:
    """Withdraw the like notification. Returns its recipient if one was removed."""
    # recipient_id first, so the delete uses the notifications_one_like index.
    return await pool.fetchval(
        """
        DELETE FROM notifications
        WHERE recipient_id = (SELECT author_id FROM posts WHERE id = $1)
          AND kind = 'like' AND post_id = $1 AND actor_id = $2
        RETURNING recipient_id
        """,
        post_id,
        actor,
    )


async def on_comment(
    pool: asyncpg.Pool, post_id: int, comment_id: int, actor: UUID
) -> tuple[UUID, asyncpg.Record] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO notifications (recipient_id, actor_id, kind, post_id, comment_id)
            SELECT author_id, $2, 'comment', id, $3 FROM posts WHERE id = $1 AND author_id <> $2
            RETURNING id, recipient_id
            """,
            post_id,
            actor,
            comment_id,
        )
        return (row["recipient_id"], await _fetch_one(conn, row["id"])) if row else None


async def on_follow(pool: asyncpg.Pool, followee: UUID, actor: UUID) -> tuple[UUID, asyncpg.Record] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO notifications (recipient_id, actor_id, kind) VALUES ($1, $2, 'follow')
            ON CONFLICT DO NOTHING
            RETURNING id, recipient_id
            """,
            followee,
            actor,
        )
        return (row["recipient_id"], await _fetch_one(conn, row["id"])) if row else None


async def on_unfollow(pool: asyncpg.Pool, followee: UUID, actor: UUID) -> UUID | None:
    return await pool.fetchval(
        "DELETE FROM notifications WHERE kind = 'follow' AND recipient_id = $1 AND actor_id = $2 RETURNING recipient_id",
        followee,
        actor,
    )


async def unread_count(pool: asyncpg.Pool, user_id: UUID) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM notifications WHERE recipient_id = $1 AND read_at IS NULL", user_id
    )


async def page(
    pool: asyncpg.Pool, user_id: UUID, before: tuple[datetime, int] | None, limit: int
) -> tuple[list[asyncpg.Record], bool]:
    after_ts, after_id = before or (None, None)
    rows = await pool.fetch(
        f"""
        SELECT {COLUMNS} {FROM}
        WHERE n.recipient_id = $1
          AND ($2::timestamptz IS NULL OR (n.created_at, n.id) < ($2, $3))
        ORDER BY n.created_at DESC, n.id DESC
        LIMIT $4
        """,
        user_id,
        after_ts,
        after_id,
        limit + 1,
    )
    return rows[:limit], len(rows) > limit


async def mark_all_read(pool: asyncpg.Pool, user_id: UUID) -> None:
    await pool.execute(
        "UPDATE notifications SET read_at = now() WHERE recipient_id = $1 AND read_at IS NULL", user_id
    )


# --- Stream tickets -----------------------------------------------------------------

TICKET_TTL = 60  # seconds: long enough to open the stream, short enough to be harmless
_local_tickets: dict[str, tuple[UUID, float]] = {}  # used when Redis is off (one instance)


async def issue_ticket(r: aioredis.Redis | None, user_id: UUID) -> str:
    ticket = secrets.token_urlsafe(32)
    if r is not None:
        await r.set(f"notify-ticket:{ticket}", str(user_id), ex=TICKET_TTL)
    else:
        now = time.monotonic()
        for t, (_, exp) in list(_local_tickets.items()):  # drop expired ones
            if exp < now:
                del _local_tickets[t]
        _local_tickets[ticket] = (user_id, now + TICKET_TTL)
    return ticket


async def redeem_ticket(r: aioredis.Redis | None, ticket: str) -> UUID | None:
    """Single use: a ticket opens one stream, then it's gone."""
    if r is not None:
        value = await r.getdel(f"notify-ticket:{ticket}")
        return UUID(value) if value else None
    entry = _local_tickets.pop(ticket, None)
    if entry is None or entry[1] < time.monotonic():
        return None
    return entry[0]
