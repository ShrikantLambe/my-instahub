from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://instahub:instahub@localhost:5432/instahub"
    # Empty means Redis is off (it is optional until Phase 4). Hosts like Render omit
    # blank env vars entirely, so the default must be "off", not localhost.
    redis_url: str = ""
    cors_origins: str = "http://localhost:3000"

    # Supabase Auth. Tokens signed with the project's asymmetric keys are checked
    # against SUPABASE_URL's JWKS; the legacy shared secret covers HS256 tokens.
    supabase_url: str = ""
    supabase_jwt_secret: str = ""

    # S3-compatible object storage: Cloudflare R2 in production, moto locally.
    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"  # R2 uses "auto"
    s3_access_key_id: str = "instahub"
    s3_secret_access_key: str = "instahub-secret"
    s3_bucket: str = "instahub"
    # Public base URL that serves objects by key (R2 public URL or custom domain).
    image_base_url: str = "http://localhost:9000/instahub"
    upload_url_ttl_seconds: int = 120  # short: a URL can be re-used until it expires
    max_upload_bytes: int = 10 * 1024 * 1024  # checked when the upload is attached

    # Abuse limits. Per-user write limits (see app/ratelimit.py) and a cap on open
    # live-update streams per instance.
    rate_limits: bool = True
    max_streams: int = 500

    # Like counter strategy (Phase 6). "direct": every like updates posts.like_count in
    # the same statement (simple, exact, but a hot post's row becomes a lock queue).
    # "buffered": likes only insert their row; counts live in Redis and a background job
    # flushes the totals to Postgres in batches. Buffered needs REDIS_URL.
    counter_mode: str = "direct"
    flush_interval_seconds: float = 2.0
    reconcile_interval_seconds: float = 900.0

    # Home feed strategy. "read": built per request from Postgres (fan-out on read).
    # "write": each new post is pushed into followers' Redis feeds (fan-out on write);
    # authors with more than FANOUT_MAX_FOLLOWERS followers are merged in at read time
    # instead. Write needs REDIS_URL. See app/feed.py and loadtest/FEED.md.
    feed_mode: str = "read"
    fanout_max_followers: int = 10_000

    @field_validator("counter_mode")
    @classmethod
    def known_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("direct", "buffered"):
            raise ValueError("COUNTER_MODE must be 'direct' or 'buffered'")
        return v

    @field_validator("feed_mode")
    @classmethod
    def known_feed_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("read", "write"):
            raise ValueError("FEED_MODE must be 'read' or 'write'")
        return v

    @field_validator("redis_url")
    @classmethod
    def upstash_needs_tls(cls, v: str) -> str:
        """Upstash only accepts TLS and closes plain connections ("Connection closed by
        server"). Its console shows `redis-cli --tls -u redis://...`, so a redis:// URL
        is easy to copy; upgrade it to rediss://."""
        v = v.strip()
        parts = urlsplit(v)
        if parts.scheme == "redis" and (parts.hostname or "").endswith(".upstash.io"):
            return "rediss" + v[len("redis"):]
        return v

    @field_validator("supabase_url")
    @classmethod
    def project_origin(cls, v: str) -> str:
        """Keep only scheme://host, so a pasted ".../rest/v1/" still yields the right
        JWKS URL and token issuer."""
        v = v.strip()
        if not v:
            return v
        parts = urlsplit(v)
        return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else v.rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
