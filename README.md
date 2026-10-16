# my-instahub

A mini Instagram on a $0 stack, built with the same blocks a real social network uses: a Next.js web client, a stateless FastAPI service, Postgres as the source of truth, Redis for cached counts and pub/sub, object storage behind a CDN for photos, and SSE for live like counts.

**Read [design.md](design.md) first.** It is the source of truth for architecture, schema and build order.

## Layout

```
api/            FastAPI service (Python, uv)
  app/          routes, config, DB pool, response schemas
  migrations/   plain SQL, applied in filename order
  scripts/      migrate.py, seed.py, reconcile.py, loadtest.py
  tests/        pytest against a real Postgres
web/            Next.js app (App Router, TypeScript)
loadtest/       k6 script for one hot post, plus RESULTS.md
docker-compose.yml   local Postgres 16, Redis 7 and an S3 stand-in (moto) for R2
```

## Run it locally

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
cp .env.example api/.env   # API settings (also copy the web lines to web/.env.local)
make up            # Postgres, Redis and local S3, plus the bucket
make api-install   # Python deps into api/.venv
make migrate       # create tables
make seed          # users alice (A), bob (B), carol (C) with follows and sample posts
make api           # http://localhost:8000  (docs at /docs)

make web-install
make web           # http://localhost:3000
```

`make test` runs the API tests in a throwaway `instahub_test` database. `make reset` wipes the DB volume and re-seeds.

Login uses your Supabase project even locally: put its URL and publishable (anon) key in `web/.env.local` and its URL in `api/.env`. Local photos live in the moto container's memory, so they disappear if it restarts; run `make bucket` to recreate the bucket.

Configuration comes from env vars; see [.env.example](.env.example).

## Deploying the web app to Vercel

The Next.js app lives in `web/`, not at the repo root, so the Vercel project needs:

- **Root Directory:** `web` (Project Settings → Build and Deployment). `web/vercel.json` pins the framework to Next.js, so the dashboard's Framework Preset doesn't matter.
- **Environment variable:** `NEXT_PUBLIC_API_URL` set to the deployed API's URL.

Until the API is hosted, the deployed site shows a "Can't reach the API" page instead of the feed.

## Deploying the API (Render + Supabase)

1. **Supabase:** create a free project. In Connect → Connection string, copy the **Session pooler** URI (Render can't reach the IPv6-only direct connection) and fill in your DB password.
2. **Render:** New → Blueprint, pick this repo. It reads `render.yaml` and creates `instahub-api`. When prompted, set `DATABASE_URL` to the Supabase URI and `CORS_ORIGINS` to your Vercel URL; leave `REDIS_URL` blank for now.
3. On boot the API runs migrations, then serves on `https://<service>.onrender.com`. (The demo users are for local development only: `make seed`.) Check `/health`: expect `"postgres":"ok"` and, with `REDIS_URL` blank, `"redis":"disabled"`. Render itself probes `/healthz`, which doesn't touch Postgres or Redis, so health checks don't spend the Upstash quota.
4. **Vercel:** set `NEXT_PUBLIC_API_URL` to that Render URL and **redeploy** (the value is baked in at build time).

### Login and photos (Phase 1)

**Supabase Auth.** In Supabase → Authentication → URL Configuration, set **Site URL** to your Vercel URL. For a quick demo, turn off **Confirm email** under Authentication → Sign In / Providers → Email, or new users must click the emailed link before logging in.

**Cloudflare R2.**
1. R2 → Create bucket (e.g. `instahub`).
2. Bucket → Settings → **Public Development URL** → Allow. That `https://pub-….r2.dev` URL is `IMAGE_BASE_URL`.
3. Bucket → Settings → **CORS policy**, so browsers can upload with presigned URLs:
   ```json
   [{"AllowedOrigins": ["https://<your-app>.vercel.app", "http://localhost:3000"],
     "AllowedMethods": ["PUT", "GET"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 3600}]
   ```
4. R2 → **Manage API tokens** → Create token with **Object Read & Write** on that bucket. Note the Access Key ID, Secret Access Key and the S3 endpoint `https://<account-id>.r2.cloudflarestorage.com`.

**Render env vars:** `SUPABASE_URL`, `S3_ENDPOINT`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET`, `IMAGE_BASE_URL` (plus `SUPABASE_JWT_SECRET` only if your project still uses the legacy JWT secret).
**Vercel env vars:** `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, then redeploy.

### Live like counts (Phase 4)

**Upstash Redis.** At console.upstash.com → Redis → **Create database** (Free plan, region near your Render region). Copy the **TLS** connection URL, `rediss://default:<password>@<name>.upstash.io:6379`, into Render's `REDIS_URL` (a `redis://` Upstash URL is upgraded to `rediss://` automatically). `/health` should then show `"redis":"ok"`. Without it the app still works: counts come from Postgres and live updates only reach viewers on the same API instance.

The free Render instance sleeps after 15 idle minutes and takes about a minute to wake, so the first page load after a break may show the error page; refresh once it's up.

### Hot posts (Phase 6)

`COUNTER_MODE` picks how likes update the count:

- `direct` (default): each like updates `posts.like_count` in the same statement. Simple and exact. Under a burst on one post, every like waits for that row's lock.
- `buffered` (needs `REDIS_URL`): a like only inserts its row. The live count lives in Redis, and a background flusher writes the totals to Postgres every `FLUSH_INTERVAL_SECONDS` (2 s) in one batched `UPDATE`. A reconcile job (every `RECONCILE_INTERVAL_SECONDS`, or `make reconcile`) recounts from the `likes` table and repairs drift.

`make loadtest` (needs [k6](https://k6.io) and a local database) compares the two. See [loadtest/RESULTS.md](loadtest/RESULTS.md): on a 4-CPU box, direct mode tops out around 770 likes/s with up to 38 sessions waiting on the hot row's lock. Buffered mode reaches 1,150/s with no lock waits. Production stays on `direct`, because the flusher's polling uses up Upstash's free command quota.

### Home feed: fan-out on read or write (Phase 10)

`FEED_MODE` picks how the home feed is built:

- `read` (default): each request queries "latest posts by everyone I follow" in Postgres.
- `write` (needs `REDIS_URL`): creating a post pushes its id into each follower's Redis feed, so opening the feed is one Redis range read plus a lookup of 10 posts. Authors with more than `FANOUT_MAX_FOLLOWERS` (10,000) followers aren't fanned out; their posts are merged in at read time. A missing feed is rebuilt from Postgres.

`make feedtest` compares the two on a 60,000-follow graph. See [loadtest/FEED.md](loadtest/FEED.md): at 300 feed opens/s, read mode's p95 was 921 ms with Postgres saturated, while write mode stayed at 10 ms. Fanning one post out to 10,000 followers took about 65 ms.

## Security and privacy

- **Row-level security** is on for every table (migration 008). Supabase serves the `public` schema over its REST API to anyone holding the anon key, which ships in the web app. With RLS on and no policies, that key can read and change nothing; the API connects as the tables' owner and is unaffected. `test_every_table_has_row_level_security` fails if a new table forgets it. In Supabase, **Advisors → Security Advisor** should show no "RLS disabled" warnings.
- **Photos are re-encoded in the browser** before upload (`web/lib/image.ts`): this strips EXIF metadata such as GPS location and device, and shrinks posts to 1,440 px and avatars to 320 px. The API refuses uploads over 10 MB when they're attached, and deletes a replaced or removed profile photo from the bucket.
- **Rate limits** per user on likes, comments, follows, uploads, new posts, profile edits and stream tickets (`app/ratelimit.py`, one Redis call per write; `RATE_LIMITS=false` turns them off). Live streams are capped at `MAX_STREAMS` (500) per instance.
- Not done yet: deleting a post or an account (and its photos), and an R2 lifecycle rule for uploads that were never attached to a post. In Cloudflare R2 → bucket → Settings → Object lifecycle rules, deleting objects after a few days under `posts/` won't work (posts live there too), so cleaning those needs a small job; it's listed as a follow-up.

## Status

| Phase | What | State |
| --- | --- | --- |
| 0 | Local skeleton: compose, FastAPI, Next.js, migrations, seed | ✅ done |
| 1 | Supabase Auth, JWT check, presigned R2 uploads, create post | ✅ done |
| 2 | Idempotent like/unlike, `viewer_has_liked`, optimistic UI | ✅ done |
| 3 | Follow/unfollow, home feed with cursor pagination | ✅ done |
| 4 | Redis cache-aside, pub/sub, SSE live counts | ✅ done |
| 5 | Deploy: Vercel, Render, Supabase, Upstash, R2 | ✅ done |
| 6 | Load-test a hot post, Redis-buffered counter, reconcile job | ✅ done |
| 7 | Comments: add, delete, cursor paging, live over SSE | ✅ done |
| 8 | Profiles: photo, bio, city, website, edit page | ✅ done |
| 9 | Notifications: likes, comments, follows; live unread badge | ✅ done |
| 10 | Home feed fan-out on write, with a celebrity hybrid; load-tested | ✅ done |

Endpoints: `GET /health`, `GET /posts`, `GET /posts/{id}`, `GET /users/{username}`, `GET /users/{username}/posts`, `GET /posts/{id}/comments?before=<cursor>` (newest first); signed in: `GET/PUT /me`, `PATCH /me` (edit username, display name, bio, city, website, `avatar_key`; only the fields sent change), `POST /uploads` (`purpose`: `post` or `avatar`), `POST /posts`, `POST/DELETE /posts/{id}/like`, `POST/DELETE /users/{username}/follow`, `GET /feed?before=<cursor>` (people you follow plus yourself, newest first), `POST /posts/{id}/comments`, `DELETE /comments/{id}` (by the comment's author or the post's author), `GET /notifications?before=<cursor>`, `GET /notifications/unread`, `POST /notifications/read`, `POST /notifications/ticket` (a 60-second single-use ticket for `GET /notifications/stream?ticket=…`, since EventSource can't send the token). Read endpoints also accept a token and then fill `viewer_has_liked` / `viewer_follows`. Live: `GET /likes?ids=1,2` (cache-aside counts) and `GET /stream?posts=1,2` (Server-Sent Events: a snapshot, then every change. `likes` events carry `like_version`; `comments` events carry `comment_count`/`comment_version` plus the added `comment` or the `deleted_id`). The seed posts point at sample image keys that don't exist, so they show a placeholder tile.
