import asyncio
import logging
import time

from sqlalchemy import func, select

from app.config.settings import redact_url, settings
from app.db.database import async_session_factory, engine
from app.models import Base, Language

logger = logging.getLogger("init_db")

# (name, ISO 639-1 code). Seeded once, only if the table is empty -- editable
# in the database afterwards without a migration or client release.
DEFAULT_LANGUAGES = [
    ("English", "en"),
    ("Hindi", "hi"),
    ("Malayalam", "ml"),
    ("Tamil", "ta"),
    ("Telugu", "te"),
    ("Kannada", "kn"),
    ("Punjabi", "pa"),
    ("Bengali", "bn"),
    ("Marathi", "mr"),
    ("Gujarati", "gu"),
    ("Spanish", "es"),
    ("Korean", "ko"),
    ("French", "fr"),
    ("Japanese", "ja"),
    ("Arabic", "ar"),
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
    await _seed_languages()


async def _create_all():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_languages():
    """Populate the language catalog on first boot only.

    A non-empty table is left untouched, so an operator's edits (adding a
    language, deactivating one) survive every subsequent restart.
    """
    async with async_session_factory() as db:
        count = (await db.execute(select(func.count()).select_from(Language))).scalar_one()
        if count:
            return
        db.add_all(Language(name=name, code=code) for name, code in DEFAULT_LANGUAGES)
        await db.commit()
        logger.info(f"Seeded {len(DEFAULT_LANGUAGES)} default languages.")
