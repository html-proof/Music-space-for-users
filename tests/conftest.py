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
def clean_catalog_queue():
    """
    The catalog write queue is a process-wide singleton holding an
    external_id -> row-id identity map. Each test gets a fresh database, so a
    map carried over from the previous one would hand out ids for rows that no
    longer exist.
    """
    from app.services.catalog_queue import CatalogWriteQueue, catalog_queue

    catalog_queue.__dict__.update(CatalogWriteQueue().__dict__)
    yield
    catalog_queue.__dict__.update(CatalogWriteQueue().__dict__)


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
def offline_gaana(monkeypatch):
    """No test reaches the real Gaana.

    Every music read now goes upstream -- there is no local-catalogue fallback
    left to absorb it -- so without this, a test that does not stub Gaana makes
    a live network call, and its result depends on what is charting today.

    The stub is installed at `_safe_request`, the single choke point every
    endpoint in `api/gaanapy.py` funnels through, and returns the library own
    no-results shape. Tests that stub a higher-level method
    (`catalog_service.gaana.get_trending`, ...) still override it, and a test
    that stubs nothing gets a deterministic "Gaana is unreachable" -- which is
    exactly the condition several of them mean to assert.
    """
    from app.services.catalog_service import catalog_service

    async def unreachable(*args, **kwargs):
        return {"error": "no results found"}

    monkeypatch.setattr(catalog_service.gaana, "_safe_request", unreachable)


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
