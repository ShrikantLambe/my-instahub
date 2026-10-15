# Phase 6: one hot post under load

The question from design.md: what happens when everyone likes the same post at once,
and does a Redis-buffered counter help?

## Setup

- `loadtest/hot_post.js` (k6, constant arrival rate). Every request is a **new** like
  from a different user on the same post, so each one has to change the count.
- `make loadtest ARGS="--rate 900 --duration 20s"` runs both modes back to back. Each
  mode gets a fresh hot post, the API with 4 uvicorn workers (pool of 10 connections
  each) and Redis on. Postgres is sampled every 50 ms for sessions waiting on a lock.
- One 4-CPU container runs everything: k6, the API, Postgres 16 and Redis 7. Absolute
  numbers are therefore modest and a bit noisy. The comparison is the point.

**direct**: `INSERT INTO likes` and `UPDATE posts SET like_count = like_count + 1` in one
statement. Every like of the hot post needs that one row's lock, so they line up.

**buffered**: insert the likes row only. Redis keeps the live count (`INCRBY`), and a
flusher adds all pending deltas to `posts.like_count` in one `UPDATE` every 2 s. A
reconcile job recounts from `likes` and repairs drift.

## Results (20 s per run)

| target likes/s | mode | achieved/s | p50 ms | p95 ms | p99 ms | dropped | lock waiters max / avg | time with lock waits | `posts` row updates |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 300 | direct | 300 | 4.6 | 10.3 | 199 | 0 | 25 / 0.23 | 1.9 % | 5,819 |
| 300 | buffered | 300 | 4.3 | 7.1 | 14 | 0 | 0 / 0 | 0 % | 446 |
| 600 | direct | 599 | 7.0 | 34.8 | 363 | 24 | 33 / 0.71 | 20.7 % | 11,611 |
| 600 | buffered | 598 | 6.0 | 35.0 | 330 | 39 | 0 / 0 | 0 % | 667 |
| 900 | direct | **768** | **218** | **580** | 886 | 2,433 | 38 / 15.3 | **78 %** | 15,247 |
| 900 | buffered | **887** | **17** | **83** | 371 | 257 | 0 / 0 | 0 % | 638 |
| 1200 | direct | 767 | 274 | 685 | 1,003 | 8,429 | 38 / 13.4 | 70 % | 15,142 |
| 1200 | buffered | 1,156 | 37 | 183 | 507 | 864 | 0 / 0 | 0 % | 526 |

Every run ended with API count = `posts.like_count` = `COUNT(*) FROM likes`, and no
request failed. "Dropped" means k6 couldn't start the request on time because the
server was too slow. `posts` row updates come from `pg_stat_user_tables` and are
approximate. In buffered mode they include the flusher's batches and the load test's
own setup.

## What it shows

- **Direct mode tops out around 770 likes/s.** Past that, the hot row is the
  bottleneck. Up to 38 sessions (nearly the whole pool of 40) wait on its lock, and
  70–80 % of samples show someone waiting. Asking for more (1200/s) adds no
  throughput, only waiting: p50 goes from 5 ms to 270 ms.
- **Buffered mode never waits on a lock** and keeps scaling until the CPUs run out:
  1,156/s with p50 37 ms on the same box. Postgres writes to the post row about once
  per flush instead of once per like: hundreds of updates instead of ~15,000.
- At low load the two are about equal. Buffered only pays off once a post is hot.
- The cost of buffered mode: `posts.like_count` lags by up to one flush interval
  (2 s), and the count lives in Redis, so Redis is now on the write path. If Redis
  loses the keys, the count is rebuilt from Postgres + pending. If a flush is lost
  mid-way, `make reconcile` (also run every 15 min by the API) recounts from `likes`.

## A bug the load test found

The first buffered run failed 0.5 % of requests at 900/s with `like counter lock busy`.
A cold post that suddenly gets hundreds of likes sends them all down the "start the
live count" path. Each one queued for the Redis lock, even after the first had already
started the count, and after 5 s the queue timed out. Waiters now re-read the count
while they wait and return as soon as it exists. The fix is
`test_first_touch_does_not_queue_behind_the_lock`, and the table above is after it.

## Production note

Render keeps `COUNTER_MODE=direct`. Upstash's free tier counts every Redis command, and
the buffered flusher polls Redis every 2 s per worker even when idle, which adds up to
roughly 40k+ commands a day. Turn it on (and raise `FLUSH_INTERVAL_SECONDS`) when a
post is actually hot.
