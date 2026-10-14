"""Apply api/migrations/*.sql in filename order, once each.

Tiny on purpose: a schema_migrations table records what ran, and each file
runs in its own transaction so a failed migration leaves nothing half-applied.
"""

import asyncio
import sys
from pathlib import Path

import asyncpg

from app.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


async def migrate(dsn: str) -> list[str]:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              name       text PRIMARY KEY,
              applied_at timestamptz DEFAULT now()
            )
            """
        )
        done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        applied = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
            applied.append(path.name)
        return applied
    finally:
        await conn.close()


def main() -> None:
    dsn = sys.argv[1] if len(sys.argv) > 1 else settings.database_url
    applied = asyncio.run(migrate(dsn))
    print("applied: " + ", ".join(applied) if applied else "already up to date")


if __name__ == "__main__":
    main()
