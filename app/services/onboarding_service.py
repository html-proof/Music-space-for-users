import logging
from datetime import datetime, timezone
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import is_uuid
from app.models.onboarding import OnboardingState
from app.models.song import Artist
from app.services.cache_service import cache_service
from app.services.library_service import library_service
from app.services.user_service import UserService
from app.utils.cache_keys import home_recommendations_key

logger = logging.getLogger("onboarding_service")


class OnboardingService:
    @staticmethod
    async def _get_or_create_state(db: AsyncSession, user_id: str) -> OnboardingState:
        stmt = select(OnboardingState).where(OnboardingState.user_id == user_id)
        res = await db.execute(stmt)
        state = res.scalar_one_or_none()
        if not state:
            state = OnboardingState(user_id=user_id)
            db.add(state)
            await db.commit()
            await db.refresh(state)
        return state

    @staticmethod
    async def get_status(db: AsyncSession, user_id: str) -> dict:
        state = await OnboardingService._get_or_create_state(db, user_id)
        pref = await UserService.get_preferences(db, user_id)
        return {
            "completed": state.completed,
            "preferred_languages": pref.preferred_languages,
            "favorite_artists": pref.favorite_artists,
            "completed_at": state.completed_at.isoformat() if state.completed_at else None,
        }

    @staticmethod
    async def set_languages(db: AsyncSession, user_id: str, languages: List[str]) -> dict:
        pref = await UserService.get_preferences(db, user_id)
        pref.preferred_languages = [lang for lang in languages if lang]
        await db.commit()
        return await OnboardingService.get_status(db, user_id)

    @staticmethod
    async def set_artists(db: AsyncSession, user_id: str, artist_ids: List[str]) -> dict:
        valid_ids = [aid for aid in artist_ids if is_uuid(aid)]
        artists: List[Artist] = []
        if valid_ids:
            stmt = select(Artist).where(Artist.id.in_(valid_ids))
            res = await db.execute(stmt)
            artists = list(res.scalars().all())

        pref = await UserService.get_preferences(db, user_id)
        pref.favorite_artists = [a.name for a in artists]
        await db.commit()

        # Selecting an artist during onboarding doubles as following them --
        # it is the same signal the library "follow" button records.
        for artist in artists:
            await library_service.follow_artist(db, user_id, artist.id)

        return await OnboardingService.get_status(db, user_id)

    @staticmethod
    async def complete(db: AsyncSession, user_id: str) -> dict:
        state = await OnboardingService._get_or_create_state(db, user_id)
        state.completed = True
        state.completed_at = datetime.now(timezone.utc)
        await db.commit()
        # Stated preferences feed the cold-start taste vector -- drop any
        # cached home feed computed before onboarding finished.
        await cache_service.delete(home_recommendations_key(user_id))
        return await OnboardingService.get_status(db, user_id)


onboarding_service = OnboardingService()
