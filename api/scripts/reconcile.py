"""Recount likes from the likes table and repair any drift in posts.like_count.

The likes table is the source of truth; posts.like_count (and the live Redis count in
buffered mode) are copies. Run it by hand, from a cron job, or let buffered mode run it
periodically (RECONCILE_INTERVAL_SECONDS).
"""

import asyncio

from app import counter, db
from app.config import settings
from app.main import make_redis


async def main() -> None:
    pool = await db.open_pool()
    r = make_redis(settings.redis_url) if settings.redis_url else None
    try:
        repaired = await counter.reconcile(pool, r)
        print(f"repaired {len(repaired)} post(s)" + (f": {repaired}" if repaired else ""))
    finally:
        if r is not None:
            await r.aclose()
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
