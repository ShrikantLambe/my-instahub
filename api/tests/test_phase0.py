from scripts.migrate import migrate
from scripts.seed import seed


async def test_health_reports_postgres(client):
    body = (await client.get("/health")).json()
    assert body["postgres"] == "ok"


async def test_seeded_posts_newest_first(client):
    posts = (await client.get("/posts")).json()
    assert len(posts) == 3
    times = [p["created_at"] for p in posts]
    assert times == sorted(times, reverse=True)
    assert all(p["like_count"] == 0 for p in posts)


async def test_get_post_and_404(client):
    first = (await client.get("/posts")).json()[0]
    resp = await client.get(f"/posts/{first['id']}")
    assert resp.status_code == 200
    assert resp.json()["author"]["username"] == first["author"]["username"]
    assert (await client.get("/posts/999999")).status_code == 404


async def test_user_profile_counts(client):
    alice = (await client.get("/users/alice")).json()
    assert alice["post_count"] == 2
    assert alice["follower_count"] == 2
    assert alice["following_count"] == 0
    assert (await client.get("/users/nobody")).status_code == 404


async def test_migrate_and_seed_are_rerunnable(client, database):
    assert await migrate(database) == []
    await seed(database)
    assert len((await client.get("/posts")).json()) == 3
