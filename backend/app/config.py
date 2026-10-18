"""Application settings, read from environment variables (see .env.example)."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEV_SECRET_KEY = "dev-only-secret-key-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR.parent / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost/competition"
    secret_key: str = DEV_SECRET_KEY
    # Set automatically by Railway; switches on production safety checks.
    railway_environment: str | None = None

    admin_email: str | None = None
    admin_password: str | None = None

    session_max_age_seconds: int = Field(default=7 * 24 * 3600, ge=300)
    timezone: str = "Europe/Bratislava"
    frontend_dist: Path = BACKEND_DIR.parent / "frontend" / "dist"

    @property
    def is_production(self) -> bool:
        return bool(self.railway_environment)

    @field_validator("secret_key")
    @classmethod
    def empty_secret_means_unset(cls, value: str) -> str:
        return value or DEV_SECRET_KEY

    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, value: str) -> str:
        # Railway provides postgresql:// (or postgres://); SQLAlchemy needs the driver name.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @model_validator(mode="after")
    def require_secret_in_production(self) -> "Settings":
        if self.is_production and (self.secret_key == DEV_SECRET_KEY or len(self.secret_key) < 32):
            raise ValueError("SECRET_KEY must be set to a random string of at least 32 characters in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
