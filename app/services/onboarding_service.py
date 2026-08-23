import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import is_uuid
from app.models.language import Language
from app.models.onboarding import OnboardingState
from app.models.song import Artist, Song as SongModel
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
    async def get_suggested_artists(db: AsyncSession, user_id: Optional[str] = None, limit: int = 30) -> List[Artist]:
        """Artists to show on the onboarding artist-selection screen.

        Backed by the real catalog (ranked by song_count) rather than a
        hardcoded list, and -- when `user_id` is given -- biased toward
        whatever the user just picked on the language-selection screen right
        before this one: onboarding is language-then-artists, so by the time
        this runs `preferred_languages` is already saved and is exactly the
        signal this screen should use instead of a fixed default.

        On a freshly deployed, still-empty catalog this falls back to pulling
        trending songs for those languages (or English/Hindi if the user has
        none saved yet) -- which upserts their artists as a side effect -- so
        onboarding never has to show an empty screen.
        """
        preferred_languages: List[str] = []
        if user_id and is_uuid(user_id):
            pref = await UserService.get_preferences(db, user_id)
            preferred_languages = [lang for lang in (pref.preferred_languages or []) if lang]

        fallback_languages = preferred_languages or ["English", "Hindi"]

        stmt = (
            select(Artist)
            .join(SongModel, SongModel.artist_id == Artist.id)
            .where(Artist.song_count > 0, SongModel.language.in_(preferred_languages))
            .distinct()
            .order_by(Artist.song_count.desc(), Artist.album_count.desc())
            .limit(limit)
        ) if preferred_languages else None

        artists: List[Artist] = []
        if stmt is not None:
            artists = list((await db.execute(stmt)).scalars().all())

        if len(artists) < min(limit, 10):
            # No language saved yet, or too few artists match it -- fall back
            # to the unfiltered top-artists ranking rather than showing (or
            # padding out) a near-empty screen.
            seen_ids = {a.id for a in artists}
            fallback_stmt = (
                select(Artist)
                .where(Artist.song_count > 0)
                .order_by(Artist.song_count.desc(), Artist.album_count.desc())
                .limit(limit)
            )
            for artist in (await db.execute(fallback_stmt)).scalars().all():
                if artist.id not in seen_ids:
                    artists.append(artist)
                    seen_ids.add(artist.id)

        if len(artists) >= min(limit, 10):
            return artists[:limit]

        seen = {a.id for a in artists}
        # Each upserted song costs several DB round trips (artist lookup-or-
        # create, album lookup-or-create, the song itself), each its own
        # commit -- on an empty/thin catalog every track is a miss, so this
        # loop is bounded by wall-clock time, not just a per-call timeout,
        # and stops the moment it has enough artists rather than always
        # working through every track of every language. The budget is well
        # under the client's request timeout so a slow pass still returns
        # something instead of the client timing out first.
        deadline = time.monotonic() + 12.0
        for language in fallback_languages:
            if len(artists) >= limit or time.monotonic() >= deadline:
                break
            # Only the raw network call is inside wait_for, never a DB
            # operation: catalog_service.get_trending mixes the Gaana fetch
            # with upserts/commits on `db`, and asyncio.wait_for cancels
            # whatever it's wrapping on timeout -- cancelling mid-commit can
            # leave an AsyncSession unusable for the rest of the request.
            # This is a "don't show an empty screen" nicety, not something
            # worth blocking on or risking the session over, so it gets a
            # tight budget and just skips the language on a miss (the search
            # box in the UI is always available regardless).
            try:
                raw = await asyncio.wait_for(catalog_service.gaana.get_trending(language, 20), timeout=4.0)
            except Exception:
                logger.warning("onboarding artist suggestions: get_trending(%s) unavailable, skipping", language)
                continue
            if not isinstance(raw, list):
                continue
            for item in raw:
                if len(artists) >= limit or time.monotonic() >= deadline:
                    break
                if not (isinstance(item, dict) and "seokey" in item):
                    continue
                song = await catalog_service.upsert_gaana_song(db, item)
                if song.artist_id and song.artist_id not in seen:
                    artist = (await db.execute(select(Artist).where(Artist.id == song.artist_id))).scalar_one_or_none()
                    if artist:
                        artists.append(artist)
                        seen.add(artist.id)
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
