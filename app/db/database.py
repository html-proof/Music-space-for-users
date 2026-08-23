import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config.settings import settings

logger = logging.getLogger("database")

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False
elif "postgres" in settings.DATABASE_URL:
    connect_args["ssl"] = "require"
    # asyncpg waits indefinitely for a TCP/TLS handshake by default. Without a
    # bound, an unreachable database host stalls startup and every request with
    # no error to show for it.
    connect_args["timeout"] = 15

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    future=True,
    connect_args=connect_args,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides an async SQLAlchemy session."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
