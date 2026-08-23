import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import is_uuid
from app.models.onboarding import OnboardingState
from app.models.song import Artist, Song as SongModel
from app.services.cache_service import cache_service
from app.services.catalog_service import catalog_service
from app.services.library_service import library_service
from app.services.user_service import UserService
from app.utils.cache_keys import home_recommendations_key

logger = logging.getLogger("onboarding_service")


@dataclass
class SuggestedArtist:
    """An artist to show on the suggestions screen, not necessarily in our DB.

    Most suggestions come straight from Gaana and are shown for browsing only
    -- persisting all of them (and their songs/albums) just to fill a
    30-artist grid is the write-amplification this type exists to avoid. Only
    an artist the user actually selects gets written, in
    `OnboardingService.set_artists`. Shaped like `app.models.song.Artist` so
    `app/api/onboarding.py` can format either the same way.
    """
    id: str
    name: str
    image_url: Optional[str] = None
    song_count: int = 0
    album_count: int = 0
    genres: list = field(default_factory=list)


def _artist_stub_from_track(raw: dict) -> Optional[SuggestedArtist]:
    """Build an unpersisted artist stub from one raw Gaana track dict.

    Mirrors the first-artist extraction `catalog_service.get_or_create_artist`
    normally does, but never touches the database -- this is for a grid the
    user is just browsing, most of which they will never select.
    """
    name = (raw.get("artists") or raw.get("artist") or "").split(",")[0].strip()
    if not name:
        return None
    seokeys = (raw.get("artist_seokeys") or "").split(",")
    ext_ids = (raw.get("artist_ids") or "").split(",")
    seokey = seokeys[0].strip() if seokeys and seokeys[0].strip() else None
    ext_id = ext_ids[0].strip() if ext_ids and ext_ids[0].strip() else (seokey or name.lower().replace(" ", "-"))
    return SuggestedArtist(
        id=ext_id,
        name=name,
        image_url=raw.get("artist_image") or None,
    )


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
    async def get_languages(db: AsyncSession) -> List[str]:
        """Selectable languages, derived from what is actually in the catalog.

        Not a maintained/seeded list: Gaana exposes no endpoint that returns
        valid language names, so the only honest source is what has actually
        been ingested into `songs` from real search/browse/trending calls. A
        freshly deployed or just-cleared catalog legitimately has none yet --
        the onboarding screen is expected to show an empty state and let the
        user skip, not fall back to a hardcoded list.
        """
        stmt = (
            select(SongModel.language)
            .where(SongModel.language.isnot(None), SongModel.language != "")
            .distinct()
            .order_by(SongModel.language)
        )
        return [row[0] for row in (await db.execute(stmt)).all()]

    @staticmethod
    async def get_suggested_artists(
        db: AsyncSession, user_id: Optional[str] = None, limit: int = 30
    ) -> List["Artist | SuggestedArtist"]:
        """Artists to show on the onboarding artist-selection screen.

        Backed by the real catalog (ranked by song_count) rather than a
        hardcoded list, and -- when `user_id` is given -- biased toward
        whatever the user just picked on the language-selection screen right
        before this one: onboarding is language-then-artists, so by the time
        this runs `preferred_languages` is already saved and is exactly the
        signal this screen should use instead of a fixed default.

        On a freshly deployed, still-empty catalog this falls back to pulling
        trending songs for those languages (or English/Hindi if the user has
        none saved yet) purely to read their artist fields -- nothing is
        written to the database here. There are far more artists on Gaana
        than this screen will ever need to persist, and most of a 30-artist
        grid is never selected, so writing all of them (plus their songs and
        albums) on every fetch is pure waste. An artist is only ever
        persisted once the user actually picks it, in `set_artists`.
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
        seen_names = {a.name.strip().lower() for a in artists if a.name}
        # No DB writes in this loop at all -- just reading artist fields off
        # the raw Gaana response -- so the only real cost per language is one
        # network call, which is why this can stay tightly time-boxed.
        deadline = time.monotonic() + 12.0
        stubs: List[SuggestedArtist] = []
        for language in fallback_languages:
            if len(artists) + len(stubs) >= limit or time.monotonic() >= deadline:
                break
            try:
                raw = await asyncio.wait_for(catalog_service.gaana.get_trending(language, 20), timeout=4.0)
            except Exception:
                logger.warning("onboarding artist suggestions: get_trending(%s) unavailable, skipping", language)
                continue
            if not isinstance(raw, list):
                continue
            for item in raw:
                if len(artists) + len(stubs) >= limit:
                    break
                if not isinstance(item, dict):
                    continue
                stub = _artist_stub_from_track(item)
                if not stub:
                    continue
                key = stub.name.strip().lower()
                if stub.id in seen or key in seen_names:
                    continue
                seen.add(stub.id)
                seen_names.add(key)
                stubs.append(stub)
        return (artists + stubs)[:limit]

    @staticmethod
    async def set_languages(db: AsyncSession, user_id: str, languages: List[str]) -> dict:
        """Stores only languages present in the catalog (case-insensitive
        match), never whatever raw strings a client happens to send -- the
        catalog is the source of truth the client is supposed to have picked
        from in the first place."""
        catalog = await OnboardingService.get_languages(db)
        catalog_by_lower = {name.lower(): name for name in catalog}

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
    async def set_artists(db: AsyncSession, user_id: str, artist_refs: List[dict]) -> dict:
        """Persists only the artists the user actually picked.

        `get_suggested_artists` deliberately never writes most of what it
        shows on the grid; this is write-on-select instead -- a ref with a
        real UUID (already in our DB) is looked up, anything else is a Gaana
        stub upserted here for the first time, one batched commit for the
        whole selection rather than one per artist.
        """
        artists: List[Artist] = []
        seen_ids = set()
        for ref in artist_refs:
            raw_id = str((ref or {}).get("id") or "").strip()
            if not raw_id or raw_id in seen_ids:
                continue
            if is_uuid(raw_id):
                artist = (
                    await db.execute(select(Artist).where(Artist.id == raw_id))
                ).scalar_one_or_none()
                if not artist:
                    continue
            else:
                name = str(ref.get("name") or "").strip()
                if not name:
                    continue
                artist = await catalog_service.get_or_create_artist(
                    db, name=name, external_id=raw_id, image_url=ref.get("image_url"),
                )
            artists.append(artist)
            seen_ids.add(raw_id)

        if artists:
            await db.commit()

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
