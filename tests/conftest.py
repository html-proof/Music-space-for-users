import os
import pytest
import pytest_asyncio
from typing import AsyncGenerator
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.db.base import Base
from app.db.database import get_db
from app.config.settings import settings
from app.middleware.rate_limit import reset_rate_limits
from app.services.cache_service import cache_service
from app.main import app

# Ensure tests use in-memory SQLite, isolated cache, and test mock tokens.
#
# Set TEST_DATABASE_URL to run this same suite against a real PostgreSQL, which
# exercises the JSONB, UUID and timestamptz behaviour that SQLite silently
# accepts -- production runs on Postgres, so this is the stricter check:
#   TEST_DATABASE_URL=postgresql+asyncpg://postgres:pw@127.0.0.1:5432/db pytest
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
settings.REDIS_ENABLED = False
settings.FIREBASE_EMULATOR_ENABLED = True
settings.APP_ENV = "development"


@pytest.fixture(autouse=True)
def clean_rate_limit_buckets():
    """
    Rate limit buckets live in process memory and are keyed by bearer token, so
    without this every test after the first ~120 requests would 429.
    """
    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture(autouse=True)
def clean_cache():
    """
    With REDIS_ENABLED off, cache_service falls back to a dict on the singleton.
    That dict outlives the per-test database, so cached ids (search results, an
    active radio station) would leak into tests whose rows no longer exist.
    """
    cache_service._memory_cache.clear()
    yield
    cache_service._memory_cache.clear()


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    # check_same_thread is a SQLite-only argument; asyncpg rejects it.
    connect_args = (
        {"check_same_thread": False} if TEST_DB_URL.startswith("sqlite") else {}
    )
    engine = create_async_engine(TEST_DB_URL, connect_args=connect_args)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test_token_user123"}


@pytest.fixture
def auth_headers_user2():
    return {"Authorization": "Bearer test_token_user456"}
