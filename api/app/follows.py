"""Follows. Like likes, the (follower_id, followee_id) primary key makes a repeat
follow a no-op, so double-taps and retries are harmless."""

from typing import NamedTuple
from uuid import UUID

import asyncpg


class UserNotFound(Exception):
    pass


async def user_id_for(conn: asyncpg.Connection, username: str) -> UUID:
    uid = await conn.fetchval("SELECT id FROM users WHERE username = $1", username)
    if uid is None:
        raise UserNotFound(username)
    return uid


async def follower_count(conn: asyncpg.Connection, user_id: UUID) -> int:
    return await conn.fetchval("SELECT count(*) FROM follows WHERE followee_id = $1", user_id)


class FollowResult(NamedTuple):
    follower_count: int
    followee: UUID
    changed: bool  # False when it was a repeat (already following / not following)


async def follow(pool: asyncpg.Pool, follower: UUID, username: str) -> FollowResult:
    """Raises ForeignKeyViolationError if the follower has no profile, CheckViolationError
    on a self-follow."""
    async with pool.acquire() as conn:
        followee = await user_id_for(conn, username)
        status = await conn.execute(
            "INSERT INTO follows (follower_id, followee_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            follower,
            followee,
        )
        return FollowResult(await follower_count(conn, followee), followee, status.endswith(" 1"))


async def unfollow(pool: asyncpg.Pool, follower: UUID, username: str) -> FollowResult:
    async with pool.acquire() as conn:
        followee = await user_id_for(conn, username)
        status = await conn.execute(
            "DELETE FROM follows WHERE follower_id = $1 AND followee_id = $2", follower, followee
        )
        return FollowResult(await follower_count(conn, followee), followee, status.endswith(" 1"))


async def is_following(pool: asyncpg.Pool, follower: UUID, followee: UUID) -> bool:
    return await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM follows WHERE follower_id = $1 AND followee_id = $2)",
        follower,
        followee,
    )
