import asyncio
import logging
from app.db.database import async_session_factory
from app.services.recommendation_service import recommendation_service
from app.services.cache_service import cache_service
from app.utils.cache_keys import home_recommendations_key

logger = logging.getLogger("recommendation_worker")


async def update_user_recommendations_job(user_id: str):
    """
    Background task to recalculate a user's home feed and refresh the cache.

    `get_home_recommendations` populates the cache itself, so this drops the
    stale entry first and lets the recompute repopulate it. Writing the payload
    here a second time duplicated that work and used a plain `model_dump()`,
    whose datetime values are not JSON-serialisable.
    """
    try:
        async with async_session_factory() as db:
            logger.info(f"Recalculating recommendations in background for user {user_id}...")
            await cache_service.delete(home_recommendations_key(user_id))
            await recommendation_service.get_home_recommendations(db, user_id)
            logger.info(f"Updated recommendations cache for user {user_id}")
    except Exception as e:
        logger.error(f"Error updating recommendations for user {user_id}: {e}")
