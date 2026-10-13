"""Seed the three demo users from design.md: A posts, B and C follow A.

Safe to re-run: users and follows use ON CONFLICT DO NOTHING, and posts are
only added for users who have none yet. Fixed UUIDs stand in for Supabase Auth
ids until Phase 1 wires up real sign-up.
"""

import asyncio
import sys
from uuid import UUID

import asyncpg

from app.config import settings

ALICE = UUID("00000000-0000-0000-0000-00000000000a")
BOB = UUID("00000000-0000-0000-0000-00000000000b")
CAROL = UUID("00000000-0000-0000-0000-00000000000c")

USERS = [
    (ALICE, "alice", "Alice (User A)"),
    (BOB, "bob", "Bob (User B)"),
    (CAROL, "carol", "Carol (User C)"),
]

# (follower, followee): B and C follow A, so A's posts land in their feeds.
FOLLOWS = [(BOB, ALICE), (CAROL, ALICE), (CAROL, BOB)]

# Phase 0 has no uploads yet, so posts point at placeholder keys.
POSTS = {
    ALICE: [
        ("Sunset from the pier", "seed/placeholder-1.jpg"),
        ("First post! Hello instahub", "seed/placeholder-2.jpg"),
    ],
    BOB: [("Coffee and code", "seed/placeholder-3.jpg")],
}


async def seed(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.executemany(
                "INSERT INTO users (id, username, display_name) VALUES ($1, $2, $3) "
                "ON CONFLICT (id) DO NOTHING",
                USERS,
            )
            await conn.executemany(
                "INSERT INTO follows (follower_id, followee_id) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                FOLLOWS,
            )
            for author, posts in POSTS.items():
                if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM posts WHERE author_id = $1)", author):
                    continue
                await conn.executemany(
                    "INSERT INTO posts (author_id, caption, image_key) VALUES ($1, $2, $3)",
                    [(author, caption, key) for caption, key in posts],
                )
    finally:
        await conn.close()


def main() -> None:
    dsn = sys.argv[1] if len(sys.argv) > 1 else settings.database_url
    asyncio.run(seed(dsn))
    print("seeded users: " + ", ".join(u[1] for u in USERS))


if __name__ == "__main__":
    main()
