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
    upload_url_ttl_seconds: int = 300

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
