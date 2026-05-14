"""
MediGuard AI — Application configuration.

Loads settings from environment variables (.env file) with sensible defaults.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application-wide settings loaded from environment / .env file."""

    # ── PostgreSQL ──────────────────────────────────────
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_user: str = Field(default="mediguard")
    postgres_password: str = Field(default="changeme")
    postgres_db: str = Field(default="mediguard_db")

    # ── Redis ───────────────────────────────────────────
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_password: str = Field(default="")
    redis_db: int = Field(default=0)

    # ── Application ────────────────────────────────────
    app_env: str = Field(default="development")
    app_debug: bool = Field(default=True)
    vitals_cache_ttl: int = Field(default=60, description="Redis TTL in seconds for cached vitals")

    @property
    def postgres_dsn(self) -> str:
        """Async PostgreSQL connection string for asyncpg / SQLAlchemy."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_sync(self) -> str:
        """Sync PostgreSQL connection string (for migrations, scripts)."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Redis connection URL."""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# Singleton instance
settings = Settings()
