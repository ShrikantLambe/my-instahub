"""Tests run against a real Postgres (docker compose up), in a throwaway database.

TEST_DATABASE_URL points at the server; a fresh instahub_test database is
created, migrated and seeded once per session.
"""

import os

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

ADMIN_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://instahub:instahub@localhost:5432/postgres"
)
TEST_DB = "instahub_test"
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"
# Must be set before app.config is imported.
os.environ["DATABASE_URL"] = TEST_URL
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_JWT_SECRET"] = "test-secret-at-least-32-bytes-long!!"
os.environ["S3_ENDPOINT"] = "https://s3.amazonaws.com"  # moto intercepts AWS endpoints
os.environ["S3_REGION"] = "us-east-1"
os.environ["S3_BUCKET"] = "instahub-test"
os.environ["IMAGE_BASE_URL"] = "https://cdn.example/instahub"
# Phase 4 needs a real Redis (docker compose up); db 15 keeps tests away from dev data.
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture(scope="session")
async def database():
    from scripts.migrate import migrate
    from scripts.seed import seed

    admin = await asyncpg.connect(ADMIN_URL)
    await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await admin.execute(f"CREATE DATABASE {TEST_DB}")
    await admin.close()
    await migrate(TEST_URL)
    await seed(TEST_URL)
    import redis.asyncio as aioredis

    r = aioredis.from_url(os.environ["REDIS_URL"])
    await r.flushdb()
    await r.aclose()
    yield TEST_URL


@pytest.fixture(scope="session")
async def client(database):
    from app.main import app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
