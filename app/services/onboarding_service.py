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
from app.models.song import Artist
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
    # Gaana's own slug for this artist, distinct from `id` (which prefers the
    # numeric artist_id): /api/catalog/artists/info specifically needs the
    # slug, so this has to survive selection or a later artist-detail fetch
    # for this artist has nothing valid to query Gaana with.
    seokey: Optional[str] = None


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
        seokey=seokey,
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
        """The languages onboarding offers, discovered from Gaana.

        Neither derived from our own `songs` rows nor hardcoded: see
        `catalog_service.get_languages`. Comes back empty only when Gaana is
        unreachable, which the client shows as a retry rather than a default
        list.
        """
        return await catalog_service.get_languages()

    @staticmethod
    async def get_suggested_artists(
        db: AsyncSession, user_id: Optional[str] = None, limit: int = 30
    ) -> List["SuggestedArtist"]:
        """Artists to show on the onboarding artist-selection screen.

        Every suggestion comes from Gaana, for the languages the user picked on
        the previous screen (onboarding is language-then-artists, so
        `preferred_languages` is already saved by the time this runs).

        This used to lead with `SELECT ... FROM artists ORDER BY song_count`,
        falling back to Gaana only when the local table was too thin. That made
        the grid a view of our own ingest: it surfaced whichever artists earlier
        requests had happened to write, in an order driven by our row counts
        rather than by what Gaana actually has for that language -- and once the
        table filled up, the Gaana path stopped running at all.

        Nothing is written to the database here. A 30-artist grid is mostly
        never selected, and persisting all of it (plus songs and albums) on
        every fetch is pure write amplification; an artist is persisted only
        when the user actually picks one, in `set_artists`.
        """
        preferred_languages: List[str] = []
        if user_id and is_uuid(user_id):
            pref = await UserService.get_preferences(db, user_id)
            preferred_languages = [lang for lang in (pref.preferred_languages or []) if lang]

        # No language chosen yet (the user skipped, or is revisiting the
        # screen). Which languages to show artists for is Gaana's call, not
        # ours: `default_languages` returns the ones it curates the most charts
        # for. Hardcoding "Hindi, English" here made the suggestion grid a
        # product decision rather than a reflection of the catalog.
        languages = preferred_languages or await catalog_service.default_languages(2)
        if not languages:
            return []

        seen_ids: set = set()
        seen_names: set = set()
        stubs: List[SuggestedArtist] = []
        deadline = time.monotonic() + 12.0

        for language in languages:
            if len(stubs) >= limit or time.monotonic() >= deadline:
                break
            try:
                raw = await asyncio.wait_for(
                    catalog_service.gaana.get_trending(language, 20), timeout=4.0
                )
            except Exception:
                logger.warning(
                    "onboarding artist suggestions: get_trending(%s) unavailable, skipping",
                    language,
                )
                continue
            if not isinstance(raw, list):
                continue
            for item in raw:
                if len(stubs) >= limit:
                    break
                if not isinstance(item, dict):
                    continue
                stub = _artist_stub_from_track(item)
                if not stub:
                    continue
                key = stub.name.strip().lower()
                if stub.id in seen_ids or key in seen_names:
                    continue
                seen_ids.add(stub.id)
                seen_names.add(key)
                stubs.append(stub)

        if stubs:
            await OnboardingService._backfill_stub_images(stubs)

        return stubs[:limit]

    # A trending track's own payload often omits `artist_image`; Gaana's
    # artist search usually has one anyway. Bounded separately from the stub
    # collection above (its own deadline, its own small per-lookup timeout,
    # capped to a handful of artists) so filling in real images can never
    # reintroduce the "onboarding takes forever" problem that same unbounded
    # per-item network pattern caused before -- this is the same class of
    # cost, deliberately kept an order of magnitude smaller.
    IMAGE_BACKFILL_LIMIT = 12
    IMAGE_BACKFILL_TIMEOUT_SECONDS = 1.5
    IMAGE_BACKFILL_BUDGET_SECONDS = 8.0

    @staticmethod
    async def _backfill_stub_images(stubs: List["SuggestedArtist"]) -> None:
        missing = [s for s in stubs if not s.image_url][:OnboardingService.IMAGE_BACKFILL_LIMIT]
        if not missing:
            return
        deadline = time.monotonic() + OnboardingService.IMAGE_BACKFILL_BUDGET_SECONDS
        for stub in missing:
            if time.monotonic() >= deadline:
                break
            try:
                results = await asyncio.wait_for(
                    catalog_service.gaana.search_artists(stub.name, 1),
                    timeout=OnboardingService.IMAGE_BACKFILL_TIMEOUT_SECONDS,
                )
            except Exception:
                continue
            if not isinstance(results, list) or not results:
                continue
            raw = results[0]
            if not isinstance(raw, dict):
                continue
            images = (raw.get("images") or {}).get("urls") or {}
            image_url = images.get("large_artwork") or images.get("medium_artwork")
            if image_url:
                stub.image_url = str(image_url)

    @staticmethod
    async def set_languages(db: AsyncSession, user_id: str, languages: List[str]) -> dict:
        """Stores only languages this service actually knows how to query Gaana
        for (case-insensitive match), never whatever raw strings a client
        happens to send.

        Validated against the same Gaana-discovered list `get_languages` served
        the client, rather than against whatever is in the songs table -- a
        language with no local rows yet is a perfectly valid choice, since its
        songs are fetched from Gaana on demand.
        """
        valid: List[str] = []
        for lang in languages:
            resolved = await catalog_service.resolve_language(lang)
            if resolved and resolved not in valid:
                valid.append(resolved)

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
                    db,
                    name=name,
                    external_id=raw_id,
                    seokey=str(ref.get("seokey") or "").strip() or None,
                    image_url=ref.get("image_url"),
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
