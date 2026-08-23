import asyncio
import logging
import time

from sqlalchemy import text

from app.config.settings import redact_url, settings
from app.db.database import engine
from app.models import Base

logger = logging.getLogger("init_db")

# (table, column, DDL type + default). `create_all` only creates missing
# TABLES, never adds a column to one that already exists -- this project has
# no Alembic, so a column added to a model after the table was first deployed
# needs an explicit, idempotent ALTER here. Postgres-only (`ADD COLUMN IF NOT
# EXISTS` is a Postgres 9.6+ extension); SQLite dev/test databases are created
# fresh from the current model on every run, so they already have the column
# and never need this.
COLUMN_MIGRATIONS = [
    ("user_preferences", "headset_safety_reminder", "BOOLEAN NOT NULL DEFAULT TRUE"),
]

# create_all issues an existence check per table plus any DDL, so it is a burst
# of round trips to the database. Bounded so an unreachable host surfaces as a
# failed startup with a readable cause instead of hanging uvicorn forever with
# "Creating database tables..." as the last line in the log.
INIT_DB_TIMEOUT_SECONDS = 90


async def init_db():
    """Initializes the database by creating all tables if they do not exist."""
    table_count = len(Base.metadata.tables)
    logger.info(
        f"Creating database tables if not present "
        f"({table_count} tables at {redact_url(settings.DATABASE_URL)})..."
    )
    started = time.monotonic()
    try:
        await asyncio.wait_for(_create_all(), timeout=INIT_DB_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as e:
        # Either bound can fire: asyncpg's connect timeout on an unreachable
        # host, or the overall wait_for. Report what actually elapsed.
        raise RuntimeError(
            f"Database schema init timed out after {time.monotonic() - started:.0f}s "
            f"against {redact_url(settings.DATABASE_URL)}. The host is unreachable "
            "or not accepting connections -- check that DATABASE_URL points at a "
            "reachable pooler host and that the password is current."
        ) from e
    logger.info(
        f"Database tables initialized successfully in "
        f"{time.monotonic() - started:.1f}s."
    )
    await _apply_column_migrations()


async def _create_all():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _apply_column_migrations():
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        for table, column, ddl in COLUMN_MIGRATIONS:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
    logger.info(f"Applied {len(COLUMN_MIGRATIONS)} column migration(s).")
