from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://instahub:instahub@localhost:5432/instahub"
    # Empty means Redis is off (it is optional until Phase 4). Hosts like Render omit
    # blank env vars entirely, so the default must be "off", not localhost.
    redis_url: str = ""
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
