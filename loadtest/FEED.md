# Phase 10: home feed, fan-out on read vs fan-out on write

How should the home feed be built: when it's read, or when posts are written?

## Setup

- `scripts/feedtest.py` (`make feedtest`) builds a follow graph in the local database:
  - 1,000 authors with 30 posts each (30,000 posts, spread over 30 days)
  - 300 readers who each follow 200 random authors (60,000 follows)
- `loadtest/feed.js` (k6) opens random readers' feeds (`GET /feed?limit=10`) at a fixed
  rate for 20 s. The API runs 4 workers with Redis on, and Postgres is sampled for busy
  queries.
- Each mode starts with no feeds in Redis. Every reader opens their feed once first
  ("first read"), then the k6 run starts.
- One 4-CPU container runs everything (k6, API, Postgres 16, Redis 7), so the numbers
  are for comparison, not absolute.

**read** (default): every request runs "latest posts by everyone I follow" in Postgres,
which merges 200 authors' posts per request.

**write**: creating a post adds it to a Redis sorted set per follower, scored by
`created_at` with the id as tie-break, so pages match read mode exactly. A request reads
11 entries from the reader's set and fetches those 10 posts by primary key.

## Reading

| feed opens/s | mode | achieved/s | p50 ms | p95 ms | p99 ms | dropped | busy Postgres queries (avg) | first read ms |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | read | 100 | 13.1 | 18.7 | 30.5 | 0 | 1.02 | 14.9 |
| 100 | write | 100 | **5.5** | **8.4** | 15.7 | 0 | **0.17** | 18.2 |
| 300 | read | 292 | 63.1 | **921** | 1,690 | 148 | **9.41** | 15.2 |
| 300 | write | 300 | **5.4** | **10.1** | 33.2 | 0 | **0.45** | 18.3 |

"Dropped" means k6 couldn't start the request on time because the server was too slow.
"First read" is the very first feed open per user: in write mode that builds the feed
from Postgres (the same query as read mode, plus the Redis writes).

## Writing: one post, fanned out

| followers (all active) | feeds written | time |
| ---: | ---: | ---: |
| 100 | 100 | 4.6 ms |
| 1,000 | 1,000 | 11.1 ms |
| 10,000 | 10,000 | 65.1 ms |
| 10,001 (over `FANOUT_MAX_FOLLOWERS`) | 0, merged at read time | 4.0 ms |

## What it shows

- **Fan-out on read gets slower as the feed is read more.** At 300 feed opens a second,
  Postgres had about 9 queries running at any moment, merging 200 authors' posts for each
  request. p95 went from 19 ms to 921 ms, and k6 dropped requests.
- **Fan-out on write keeps reads flat.** A feed open is a range read on one Redis key
  plus a primary-key lookup of 10 posts, 8–10 ms at p95 at either rate, with Postgres
  nearly idle.
- **The cost moves to the write side.** It grows with the author's follower count: about
  65 ms and 20 Redis calls for 10,000 followers, per post. A person with millions of
  followers would cost millions of writes per post, which is why the hybrid exists.
  Above `FANOUT_MAX_FOLLOWERS` (10,000) an author's posts aren't fanned out. Readers who
  follow them pick their recent posts up at read time with one indexed query.
- **Other costs:** Redis memory (up to 500 ids per active user), feeds that are briefly
  stale if a fan-out fails (they heal when the 7-day TTL expires and the feed is
  rebuilt), and a slightly slower very first read while a user's feed is built.

## Production note

Render keeps `FEED_MODE=read`. At this app's traffic Postgres answers the feed in a few
milliseconds, and every Upstash command counts against the free quota: each feed open
costs about 4 Redis commands in write mode, and each post costs 1 per 500 followers.
Switch it on when feed reads become the bottleneck.
