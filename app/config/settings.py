import os
from typing import List, Optional, Union
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Environments that must never accept mock authentication tokens.
PRODUCTION_ENVS = {"production", "prod", "staging", "stage"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # App Information
    APP_NAME: str = "MusicAppBackend"
    APP_ENV: str = "development"
    DEBUG: bool = True
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./music_app.db"
    DB_ECHO: bool = False

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_database_url(cls, v: Optional[str], info) -> str:
        if v:
            if v.startswith("postgresql://"):
                return v.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif v.startswith("postgres://"):
                return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v or "sqlite+aiosqlite:///./music_app.db"

    # Supabase (Optional)
    SUPABASE_URL: Optional[str] = None
    SUPABASE_ANON_KEY: Optional[str] = None
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = None

    # Redis / Upstash Cache & Pub/Sub
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = False
    UPSTASH_REDIS_REST_URL: Optional[str] = None
    UPSTASH_REDIS_REST_TOKEN: Optional[str] = None

    @field_validator("REDIS_URL", mode="before")
    @classmethod
    def assemble_redis_url(cls, v: Optional[str], info) -> str:
        # If explicit REDIS_URL is given and not default localhost, use it
        if v and v != "redis://localhost:6379/0":
            return v
        return v or "redis://localhost:6379/0"

    def get_active_redis_url(self) -> str:
        if self.UPSTASH_REDIS_REST_URL and self.UPSTASH_REDIS_REST_TOKEN:
            host = self.UPSTASH_REDIS_REST_URL.replace("https://", "").replace("http://", "").strip("/")
            return f"rediss://default:{self.UPSTASH_REDIS_REST_TOKEN}@{host}:6379"
        return self.REDIS_URL

    # Firebase Authentication
    FIREBASE_PROJECT_ID: Optional[str] = None
    FIREBASE_CLIENT_EMAIL: Optional[str] = None
    FIREBASE_PRIVATE_KEY: Optional[str] = None
    FIREBASE_CREDENTIALS_PATH: Optional[str] = None
    # Accepts mock "test_token_<uid>" bearer tokens. Local development only:
    # enabling this in a production environment is refused at startup.
    FIREBASE_EMULATOR_ENABLED: bool = False

    # CORS
    CORS_ORIGINS: Union[List[str], str] = ["*"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, (list, str)):
            return v
        return ["*"]

    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() in PRODUCTION_ENVS

    def cors_allows_wildcard(self) -> bool:
        return "*" in (self.CORS_ORIGINS or [])

    @model_validator(mode="after")
    def refuse_mock_auth_in_production(self) -> "Settings":
        """
        Fail closed: a production deployment that also accepts mock tokens has no
        authentication at all, so refuse to boot rather than serve every account
        to anyone who can guess a uid.
        """
        if self.FIREBASE_EMULATOR_ENABLED and self.is_production():
            raise ValueError(
                f"FIREBASE_EMULATOR_ENABLED must be False when APP_ENV={self.APP_ENV!r}. "
                "Mock authentication tokens are accepted while it is on, which would "
                "allow anyone to impersonate any user."
            )
        return self

    # Recommendation Rules
    MIN_LISTEN_SECONDS: int = 30
    MIN_COMPLETION_PERCENTAGE: int = 50
    RECOMMENDATION_CACHE_TTL_SECONDS: int = 3600

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 120
    # Set to True when running behind a reverse proxy (Render, nginx, Cloudflare)
    # so rate limiting keys off the real client IP instead of the proxy's.
    TRUST_PROXY_HEADERS: bool = False


settings = Settings()
