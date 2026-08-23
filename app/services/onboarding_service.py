import logging
from datetime import datetime, timezone
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import is_uuid
from app.models.language import Language
from app.models.onboarding import OnboardingState
from app.models.song import Artist
from app.services.cache_service import cache_service
from app.services.catalog_service import catalog_service
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
    async def get_languages(db: AsyncSession) -> List[Language]:
        stmt = select(Language).where(Language.is_active.is_(True)).order_by(Language.name)
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def get_suggested_artists(db: AsyncSession, limit: int = 30) -> List[Artist]:
        """Artists to show on the onboarding artist-selection screen.

        Backed by the real catalog (ranked by song_count) rather than a
        hardcoded list. On a freshly deployed, still-empty catalog this falls
        back to pulling trending songs for a couple of default languages --
        which upserts their artists as a side effect -- so onboarding never
        has to show an empty screen.
        """
        stmt = (
            select(Artist)
            .where(Artist.song_count > 0)
            .order_by(Artist.song_count.desc(), Artist.album_count.desc())
            .limit(limit)
        )
        artists = list((await db.execute(stmt)).scalars().all())
        if len(artists) >= min(limit, 10):
            return artists

        seen = {a.id for a in artists}
        for language in ("English", "Hindi"):
            songs = await catalog_service.get_trending(db, language=language, limit=20)
            for song in songs:
                if song.artist_id and song.artist_id not in seen:
                    artist = (await db.execute(select(Artist).where(Artist.id == song.artist_id))).scalar_one_or_none()
                    if artist:
                        artists.append(artist)
                        seen.add(artist.id)
            if len(artists) >= limit:
                break
        return artists[:limit]

    @staticmethod
    async def set_languages(db: AsyncSession, user_id: str, languages: List[str]) -> dict:
        """Stores only languages present in the catalog (case-insensitive
        match), never whatever raw strings a client happens to send -- the
        catalog is the source of truth the client is supposed to have picked
        from in the first place."""
        catalog = await OnboardingService.get_languages(db)
        catalog_by_lower = {lang.name.lower(): lang.name for lang in catalog}

        valid: List[str] = []
        for lang in languages:
            canonical = catalog_by_lower.get((lang or "").strip().lower())
            if canonical and canonical not in valid:
                valid.append(canonical)

        if not valid:
            raise ValueError("No valid languages selected. Choose from GET /api/onboarding/languages.")

        pref = await UserService.get_preferences(db, user_id)
        pref.preferred_languages = valid
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
