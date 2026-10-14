import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None


async def open_pool(dsn: str | None = None) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(dsn or settings.database_url, min_size=1, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool is not open")
    return _pool
