import os
from typing import List, Optional, Union
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Environments that must never accept mock authentication tokens.
PRODUCTION_ENVS = {"production", "prod", "staging", "stage"}

# Sentinel for "no Redis was configured". Anything else counts as explicit.
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

# Markers left behind by scripts/gen_render_env.py and .env.example. A URL whose
# password is one of these was uploaded before the real secret was pasted in,
# and Redis reports it as an ordinary auth failure that reads like a rotation
# problem -- so it is named explicitly at startup instead.
REDIS_PLACEHOLDER_MARKERS = (
    "ROTATED_UPSTASH_TOKEN",
    "ROTATED_PASSWORD",
    "your-upstash-token",
    "[password]",
    "CHANGEME",
)


def redact_url(url: Optional[str]) -> str:
    """
    Return `url` with the password replaced by '***' so a connection URL can be
    logged. Diagnosing a connection failure needs the host and username; the
    secret itself must never reach the log.
    """
    if not url:
        return "<unset>"
    scheme, _, rest = url.partition("://")
    if not rest:
        return "<malformed>"
    credentials, sep, host = rest.rpartition("@")
    if not sep:
        return url
    user, has_pw, _ = credentials.partition(":")
    return f"{scheme}://{user}{':***' if has_pw else ''}@{host}"


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
    REDIS_URL: str = DEFAULT_REDIS_URL
    REDIS_ENABLED: bool = False
    UPSTASH_REDIS_REST_URL: Optional[str] = None
    UPSTASH_REDIS_REST_TOKEN: Optional[str] = None

    @field_validator("REDIS_URL", mode="before")
    @classmethod
    def normalize_redis_url(cls, v: Optional[str], info) -> str:
        # A URL pasted into a hosting dashboard often arrives wrapped in quotes
        # or with a trailing newline. Redis sends everything after the colon as
        # the password verbatim, so one stray character comes back as
        # "invalid username-password pair" -- an auth error that looks nothing
        # like the copy/paste mistake that caused it.
        if not isinstance(v, str):
            return DEFAULT_REDIS_URL
        cleaned = v.strip().strip("\"'").strip()
        return cleaned or DEFAULT_REDIS_URL

    def has_explicit_redis_url(self) -> bool:
        return bool(self.REDIS_URL) and self.REDIS_URL != DEFAULT_REDIS_URL

    def get_active_redis_url(self) -> str:
        # An explicitly configured REDIS_URL always wins. The Upstash REST pair
        # is only a fallback, because UPSTASH_REDIS_REST_TOKEN authenticates the
        # HTTP REST API and is not necessarily the RESP protocol password -- so
        # a rediss:// URL derived from it can be rejected at AUTH even when both
        # REST values are correct. Deriving it used to take precedence, which
        # silently overrode a working REDIS_URL.
        if self.has_explicit_redis_url():
            return self.REDIS_URL
        if self.UPSTASH_REDIS_REST_URL and self.UPSTASH_REDIS_REST_TOKEN:
            host = self.UPSTASH_REDIS_REST_URL.replace("https://", "").replace("http://", "").strip("/")
            return f"rediss://default:{self.UPSTASH_REDIS_REST_TOKEN}@{host}:6379"
        return self.REDIS_URL

    def redis_url_source(self) -> str:
        """Which setting produced the active URL, for startup diagnostics."""
        if self.has_explicit_redis_url():
            return "REDIS_URL"
        if self.UPSTASH_REDIS_REST_URL and self.UPSTASH_REDIS_REST_TOKEN:
            return "UPSTASH_REDIS_REST_URL/TOKEN (derived)"
        return "default (localhost)"

    def redis_url_placeholder(self) -> Optional[str]:
        """The placeholder marker found in the active Redis URL, if any."""
        url = self.get_active_redis_url()
        return next((m for m in REDIS_PLACEHOLDER_MARKERS if m in url), None)

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

    # Machine Learning
    # The master switch. False routes every surface -- home shelves, search
    # ranking, autoplay, similar songs -- back to the pre-ML heuristics, so a
    # misbehaving model can be disabled without a redeploy of application code.
    ML_ENABLED: bool = True
    # Retrain on a background loop inside the web process. Off by default: on a
    # single small instance a training pass competes with request handling, so
    # prefer the Render cron job (scripts/train_ml.py) in production.
    ML_AUTO_TRAIN_ENABLED: bool = False
    ML_TRAIN_INTERVAL_MINUTES: int = 360
    # Below this many labelled rows a trained model is never promoted. The gate
    # matters more than the trainer here: with a few dozen interactions a fitted
    # model is noise, and the hand-tuned prior is genuinely better.
    ML_MIN_TRAINING_SAMPLES: int = 200
    # Shared secret for POST /api/ml/retrain. There is no admin role in
    # firebase_auth.py to hang authorization off, and an unauthenticated retrain
    # endpoint is a free denial-of-service -- so with no token set, the endpoint
    # refuses every request rather than defaulting to open.
    ML_ADMIN_TOKEN: Optional[str] = None
    # Shared secret for writing lyrics (POST/DELETE /api/songs/{id}/lyrics), same
    # rationale as ML_ADMIN_TOKEN: no admin role to hang authorization off, and
    # reads stay open to any authenticated user.
    LYRICS_ADMIN_TOKEN: Optional[str] = None

    # Periodic check for new songs by followed artists, which fan out as
    # notifications (see app/workers/release_watch_worker.py). Off by default
    # for the same reason as ML_AUTO_TRAIN_ENABLED: on a single small instance
    # it should run as a separate Render cron job (scripts/check_new_releases.py)
    # rather than compete with request handling in-process.
    RELEASE_WATCH_ENABLED: bool = False
    RELEASE_WATCH_INTERVAL_MINUTES: int = 180
    # Fraction of each shelf given to unheard songs. Zero makes the feed
    # self-reinforcing: the ranker never sees evidence about songs it scores low.
    ML_EXPLORATION_RATE: float = 0.10
    ML_EMBEDDING_DIM: int = 256
    ML_CANDIDATE_LIMIT: int = 400

    @field_validator("ML_EXPLORATION_RATE")
    @classmethod
    def clamp_exploration_rate(cls, v: float) -> float:
        return min(max(float(v), 0.0), 0.5)

    @field_validator("ML_EMBEDDING_DIM")
    @classmethod
    def check_embedding_dim(cls, v: int) -> int:
        # Changing this invalidates every persisted item_sim artifact, because a
        # song's vector -- and therefore every similarity computed from it -- is a
        # function of the dimension. Retrain after changing it.
        if v < 32:
            raise ValueError("ML_EMBEDDING_DIM must be at least 32; hash collisions swamp the signal below that")
        return v

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 120
    # Set to True when running behind a reverse proxy (Render, nginx, Cloudflare)
    # so rate limiting keys off the real client IP instead of the proxy's.
    TRUST_PROXY_HEADERS: bool = False


settings = Settings()
