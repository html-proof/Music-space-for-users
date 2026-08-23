import logging
from app.db.database import engine
from app.models import Base

logger = logging.getLogger("init_db")


async def init_db():
    """Initializes the database by creating all tables if they do not exist."""
    async with engine.begin() as conn:
        logger.info("Creating database tables if not present...")
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized successfully.")
