import asyncio
import logging
from app.db.database import async_session_factory
from app.services.recommendation_service import recommendation_service
from app.services.cache_service import cache_service

logger = logging.getLogger("recommendation_worker")


async def update_user_recommendations_job(user_id: str):
    """Background task to recalculate user recommendations and refresh Redis cache."""
    try:
        async with async_session_factory() as db:
            logger.info(f"Recalculating recommendations in background for user {user_id}...")
            recs = await recommendation_service.get_home_recommendations(db, user_id)
            # Store in cache
            cache_key = f"recommendations:user:{user_id}"
            await cache_service.set_json(cache_key, recs.model_dump(), ttl_seconds=3600)
            logger.info(f"Updated recommendations cache for user {user_id}")
    except Exception as e:
        logger.error(f"Error updating recommendations for user {user_id}: {e}")
