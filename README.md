# my-instahub

A mini Instagram on a $0 stack, built with the same blocks a real social network uses: a Next.js web client, a stateless FastAPI service, Postgres as the source of truth, Redis for cached counts and pub/sub, object storage behind a CDN for photos, and SSE for live like counts.

**Read [design.md](design.md) first.** It is the source of truth for architecture, schema and build order.

## Layout

```
api/            FastAPI service (Python, uv)
  app/          routes, config, DB pool, response schemas
  migrations/   plain SQL, applied in filename order
  scripts/      migrate.py, seed.py
  tests/        pytest against a real Postgres
web/            Next.js app (App Router, TypeScript)
docker-compose.yml   local Postgres 16 + Redis 7
```

## Run it locally

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
make up            # Postgres + Redis
make api-install   # Python deps into api/.venv
make migrate       # create tables
make seed          # users alice (A), bob (B), carol (C) with follows and sample posts
make api           # http://localhost:8000  (docs at /docs)

make web-install
make web           # http://localhost:3000
```

`make test` runs the API tests in a throwaway `instahub_test` database. `make reset` wipes the DB volume and re-seeds.

Configuration comes from env vars; see [.env.example](.env.example).

## Deploying the web app to Vercel

The Next.js app lives in `web/`, not at the repo root, so the Vercel project needs:

- **Root Directory:** `web` (Project Settings → Build and Deployment). `web/vercel.json` pins the framework to Next.js, so the dashboard's Framework Preset doesn't matter.
- **Environment variable:** `NEXT_PUBLIC_API_URL` set to the deployed API's URL.

Until the API is hosted, the deployed site shows a "Can't reach the API" page instead of the feed.

## Deploying the API (Render + Supabase)

1. **Supabase:** create a free project. In Connect → Connection string, copy the **Session pooler** URI (Render can't reach the IPv6-only direct connection) and fill in your DB password.
2. **Render:** New → Blueprint, pick this repo. It reads `render.yaml` and creates `instahub-api`. When prompted, set `DATABASE_URL` to the Supabase URI and `CORS_ORIGINS` to your Vercel URL; leave `REDIS_URL` blank for now.
3. On boot the API runs migrations and seeds the demo users, then serves on `https://<service>.onrender.com`. Check `/health`: expect `"postgres":"ok"` and, with `REDIS_URL` blank, `"redis":"disabled"`.
4. **Vercel:** set `NEXT_PUBLIC_API_URL` to that Render URL and **redeploy** (the value is baked in at build time).

The free Render instance sleeps after 15 idle minutes and takes about a minute to wake, so the first page load after a break may show the error page; refresh once it's up.

## Status

| Phase | What | State |
| --- | --- | --- |
| 0 | Local skeleton: compose, FastAPI, Next.js, migrations, seed | ✅ done |
| 1 | Supabase Auth, JWT check, presigned R2 uploads, create post | next |
| 2 | Idempotent like/unlike, `viewer_has_liked`, optimistic UI | |
| 3 | Follow/unfollow, home feed with cursor pagination | |
| 4 | Redis cache-aside, pub/sub, SSE live counts | |
| 5 | Deploy: Vercel, Render, Supabase, Upstash, R2 | |
| 6 | Load-test a hot post, Redis-buffered counter | |

Phase 0 endpoints: `GET /health`, `GET /posts`, `GET /posts/{id}`, `GET /users/{username}`. Posts use placeholder image keys until uploads land in Phase 1.
