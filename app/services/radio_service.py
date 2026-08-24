"""
Endless radio stations, and the autoplay that keeps a queue from dead-ending.

Station state lives in the cache rather than a new database column: it is
disposable session state, so it needs no schema migration, and Redis (unlike
process memory) survives the free-tier web service spinning down after idle.
With Redis disabled the station simply is not remembered between requests --
batches are still generated, they just repeat themselves more often.

Batches are built on demand inside the request. Nothing here needs a background
worker or a cron job, neither of which exists on the free plan.
"""
import logging
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.base import is_uuid
from app.models.history import ListeningHistory
from app.models.song import Artist, Song
from app.services.cache_service import cache_service
from app.services.catalog_queue import catalog_queue
from app.services.catalog_service import catalog_service
from app.services.recommendation_service import RecommendationService
from app.utils.cache_keys import radio_station_key

logger = logging.getLogger("radio_service")

SEED_TYPES = ("song", "artist", "mood", "personalized")
DEFAULT_BATCH_SIZE = 20
STATION_TTL_SECONDS = 6 * 60 * 60
# A station only remembers so many already-played tracks. Unbounded, the value
# would grow for as long as the listener keeps skipping.
MAX_SERVED_REMEMBERED = 200
# How many recently heard tracks to keep out of a fresh batch.
RECENT_HISTORY_WINDOW = 50


class RadioService:
    # ------------------------------------------------------------------ state

    @staticmethod
    async def get_station(user_id: str) -> Optional[Dict[str, Any]]:
        station = await cache_service.get_json(radio_station_key(user_id))
        if isinstance(station, dict) and station.get("seed_type") in SEED_TYPES:
            return station
        return None

    @staticmethod
    async def set_station(
        user_id: str,
        seed_type: str,
        seed_id: Optional[str],
        served: Sequence[str]
    ) -> Dict[str, Any]:
        # Keep the most recent ids: those are the ones worth not repeating.
        trimmed = list(dict.fromkeys(str(s) for s in served if s))[-MAX_SERVED_REMEMBERED:]
        station = {"seed_type": seed_type, "seed_id": seed_id, "served": trimmed}
        await cache_service.set_json(
            radio_station_key(user_id), station, ttl_seconds=STATION_TTL_SECONDS
        )
        return station

    @staticmethod
    async def clear_station(user_id: str) -> None:
        await cache_service.delete(radio_station_key(user_id))

    # ------------------------------------------------------------------ seeds

    @staticmethod
    async def resolve_seed(db: AsyncSession, seed_type: str, seed_id: Optional[str]) -> Optional[str]:
        """
        Normalise a client-supplied seed, or return None when it cannot be
        resolved. Seed ids arrive straight from the request body, so they are
        screened here instead of being bound to a uuid column, which would turn
        an unknown seed into a 500.
        """
        if seed_type == "personalized":
            return None

        value = (seed_id or "").strip()
        if not value:
            return None
        if seed_type == "mood":
            return value
        if seed_type == "song":
            song = await catalog_service.get_song_by_id(db, value)
            return song.id if song else None
        if seed_type == "artist":
            artist = await RadioService._find_artist(db, value)
            if artist:
                return artist.id
            # No local artist row -- which only means the user has never
            # followed or picked this artist, not that the artist is unknown.
            # Ask Gaana whether the name resolves to anything before accepting
            # it, so a typo still gets SEED_NOT_FOUND rather than an empty
            # station. This used to be `SELECT ... FROM songs WHERE artist_name
            # ILIKE`, which rejected every artist our own ingest had not
            # happened to cover. `get_artist_top_songs` caches, so the
            # `_artist_songs` call that follows is a cache hit, not a second
            # round trip.
            try:
                probe = await catalog_service.get_artist_top_songs(db, value, limit=1)
            except Exception:
                logger.warning("artist seed probe for %s failed", value, exc_info=True)
                return None
            return value if probe else None
        return None

    @staticmethod
    async def _find_artist(db: AsyncSession, value: str) -> Optional[Artist]:
        # Matching is by external key / seokey / name, so a queued artist would
        # look like an unknown seed and 404 rather than start a station.
        await catalog_queue.ensure_kind_persisted(db, "artist")
        conditions = [
            Artist.external_id == value,
            Artist.seokey == value,
            Artist.name.ilike(value),
        ]
        # Artist.id is a native uuid on PostgreSQL; only compare when it parses.
        if is_uuid(value):
            conditions.append(Artist.id == value)
        res = await db.execute(select(Artist).where(or_(*conditions)).limit(1))
        return res.scalars().first()

    # ---------------------------------------------------------------- batches

    @staticmethod
    async def build_batch(
        db: AsyncSession,
        user_id: str,
        seed_type: str,
        seed_id: Optional[str] = None,
        exclude_ids: Optional[Iterable[str]] = None,
        limit: int = DEFAULT_BATCH_SIZE,
        allow_network: bool = False
    ) -> List[Song]:
        """
        Next stretch of a station.

        `allow_network` is for starting a station, where one upstream round trip
        is acceptable. Autoplay refills leave it off so that pressing skip is
        never waiting on Gaana.
        """
        if seed_type not in SEED_TYPES:
            seed_type = "personalized"
        exclude: Set[str] = {str(i) for i in (exclude_ids or []) if i}

        candidates = await RadioService._seed_candidates(
            db, user_id, seed_type, seed_id, limit, allow_network
        )

        # Recently heard tracks make poor radio, but on a small catalogue
        # dropping them can empty the batch -- so they are only a preference.
        recent = await RadioService._recent_song_ids(db, user_id)
        picks = RadioService._collect(candidates, exclude | recent)
        if len(picks) < limit:
            picks = RadioService._collect(candidates, exclude, into=picks)

        if len(picks) < limit:
            padding = await RadioService._padding(db, limit, allow_network)
            picks = RadioService._collect(padding, exclude, into=picks)

        return RadioService._popularity_shuffle(picks)[:limit]

    @staticmethod
    def _collect(
        songs: Iterable[Optional[Song]],
        excluded: Set[str],
        into: Optional[List[Song]] = None
    ) -> List[Song]:
        """Append `songs`, skipping excluded ids and anything already present."""
        out: List[Song] = list(into or [])
        seen = {s.id for s in out} | set(excluded)
        for song in songs:
            if song is None or song.id in seen:
                continue
            seen.add(song.id)
            out.append(song)
        return out

    @staticmethod
    def _popularity_shuffle(songs: Sequence[Song]) -> List[Song]:
        # Well-played tracks surface more often without the station collapsing
        # into the same fixed list on every request.
        return sorted(
            songs,
            key=lambda s: ((s.play_count or 0) + 1) * random.uniform(0.5, 1.5),
            reverse=True
        )

    @staticmethod
    async def _recent_song_ids(db: AsyncSession, user_id: str) -> Set[str]:
        stmt = (
            select(ListeningHistory.song_id)
            .where(ListeningHistory.user_id == user_id)
            .order_by(desc(ListeningHistory.started_at))
            .limit(RECENT_HISTORY_WINDOW)
        )
        res = await db.execute(stmt)
        return {sid for sid in res.scalars().all() if sid}

    @staticmethod
    async def _seed_candidates(
        db: AsyncSession,
        user_id: str,
        seed_type: str,
        seed_id: Optional[str],
        limit: int,
        allow_network: bool
    ) -> List[Song]:
        # Over-fetch: much of this pool is dropped as already-served or recent.
        pool = max(limit * 3, limit)

        if seed_type == "song" and seed_id:
            songs = await RecommendationService.get_similar_songs(db, seed_id, limit=pool, allow_network=allow_network)
            if allow_network and len(songs) < limit:
                songs = songs + await RadioService._upstream_for_song(db, seed_id, pool)
            return songs

        if seed_type == "artist" and seed_id:
            songs = await RadioService._artist_songs(db, seed_id, pool)
            if allow_network and len(songs) < limit:
                songs = songs + await RadioService._upstream_for_artist(db, seed_id, pool)
            return songs

        if seed_type == "mood" and seed_id:
            return await RecommendationService.get_mood_mix(db, seed_id, limit=pool)

        return await RadioService._personalized_songs(db, user_id, pool)

    @staticmethod
    async def _artist_songs(db: AsyncSession, seed_id: str, limit: int) -> List[Song]:
        """The artist current catalog, from Gaana.

        The local `artists` row (if any) supplies the display name to query
        with -- it records that the user follows this artist, which is user
        data. The tracks themselves are never read out of `songs`, which
        previously capped an artist station at whatever we had ingested for
        them and ordered it by our own play counts.
        """
        artist = await RadioService._find_artist(db, seed_id)
        name = (artist.name if artist else seed_id) or ""
        if not name.strip():
            return []
        return list(await catalog_service.get_artist_top_songs(db, name.strip(), limit=limit))

    @staticmethod
    async def _personalized_songs(db: AsyncSession, user_id: str, limit: int) -> List[Song]:
        """
        The personalized station's candidate pool.

        Prefers the ranked ML pipeline, which is what makes an endless station
        feel like it knows the listener: the SQL path below can only filter on the
        user's top genres and artists, so it returns the same rows in the same
        popularity order every time. Any failure -- or ML_ENABLED off -- falls
        through to that heuristic, because a station that plays something is worth
        more than one that errors.
        """
        if settings.ML_ENABLED:
            try:
                songs = await RadioService._ml_personalized_songs(db, user_id, limit)
                if songs:
                    return songs
            except Exception:
                logger.exception("ML station pool failed for %s; using heuristics", user_id)

        return await RadioService._heuristic_personalized_songs(db, user_id, limit)

    @staticmethod
    async def _ml_personalized_songs(db: AsyncSession, user_id: str, limit: int) -> List[Song]:
        """Ranked, diversified continuation for one listener.

        Exploration is left off: `build_batch` already drops recently heard tracks
        and the caller excludes everything the station has served, so the batch is
        novel by construction. Adding ε-greedy swaps on top would only trade a
        well-ranked track for a random one.
        """
        from app.ml import candidates as ml_candidates
        from app.ml import diversify, ranker as ml_ranker
        from app.ml.user_state import build_user_state, load_song_stats, max_play_count

        if not is_uuid(user_id):
            return []

        state = await build_user_state(db, user_id)
        pool = await ml_candidates.generate(db, state, limit=min(limit * 4, 400))
        if len(pool) == 0:
            return []

        stats = await load_song_stats(db, list(pool.songs.keys()))
        active_ranker = await ml_ranker.load_ranker(db)
        scored = active_ranker.rank(
            pool.song_list(),
            state,
            sources=pool.source_map(),
            cf_scores=pool.cf_scores,
            stats=stats,
            max_play_count=await max_play_count(db),
            vectors=pool.vectors,
        )
        final = diversify.finalize(
            scored,
            limit=limit,
            played_song_ids=set(state.play_counts.keys()),
            max_per_artist=3,
            exploration_rate=0.0,
        )
        return [s.song for s in final]

    @staticmethod
    async def _heuristic_personalized_songs(db: AsyncSession, user_id: str, limit: int) -> List[Song]:
        """Non-ML personalized pool: the user top artists and genres, fetched
        from Gaana.

        The affinities come from Postgres -- history, likes, and the artists and
        languages picked at onboarding -- and are used purely as *query terms*.
        This used to be `WHERE genre IN (...) OR artist_name IN (...) ORDER BY
        play_count`, which could only ever return rows already sitting in our
        database, so the same station repeated indefinitely.
        """
        affinities = await RecommendationService.calculate_user_affinities(db, user_id)
        languages = affinities.get("top_languages") or []
        language = languages[0] if languages else None

        songs: List[Song] = []
        seen: Set[str] = set()

        async def take(fetch, what: str) -> None:
            if len(songs) >= limit:
                return
            try:
                fetched = await fetch()
            except Exception:
                logger.warning("radio pool fetch (%s) failed; continuing", what, exc_info=True)
                return
            for song in fetched:
                if str(song.id) in seen:
                    continue
                seen.add(str(song.id))
                songs.append(song)

        for artist in (affinities.get("top_artists") or [])[:3]:
            await take(
                lambda a=artist: catalog_service.get_artist_top_songs(db, a, limit=limit),
                "artist:%s" % artist,
            )
        for genre in (affinities.get("top_genres") or [])[:2]:
            await take(
                lambda g=genre: catalog_service.get_genre_or_mood_songs(
                    db, g, language, limit=limit
                ),
                "genre:%s" % genre,
            )
        for mood in (affinities.get("top_moods") or [])[:1]:
            await take(
                lambda m=mood: catalog_service.get_genre_or_mood_songs(
                    db, m, language, limit=limit
                ),
                "mood:%s" % mood,
            )

        return songs[:limit]

    @staticmethod
    async def _padding(db: AsyncSession, limit: int, allow_network: bool) -> List[Song]:
        """
        Last resort so a station does not come back empty: whatever is trending
        on Gaana right now.

        `allow_network` no longer gates this. It used to, with a
        `SELECT ... ORDER BY play_count` behind it for the autoplay case -- the
        point being that pressing skip should never wait on Gaana. That fallback
        was the database acting as a catalog, and it made every station converge
        on the same locally-popular rows. `catalog_service.get_trending` caches
        each language for 30 minutes, so the refill a skip triggers is served
        from cache in the overwhelming majority of cases; when it genuinely is
        not, an empty batch (and a retry) is the honest outcome.
        """
        # Over-fetch: everything the station has already served is filtered out
        # of this list afterwards, so asking for exactly `limit` reliably yields
        # nothing once a listener is a batch or two deep. The old DB query took
        # `limit * 2` for the same reason.
        try:
            return list(await catalog_service.get_trending(db, limit=limit * 3))
        except Exception as e:
            logger.warning(f"Radio padding from trending failed: {e}")
            return []

    # --------------------------------------------------------------- upstream

    @staticmethod
    async def _upstream_for_song(db: AsyncSession, seed_song_id: str, limit: int) -> List[Song]:
        song = await catalog_service.get_song_by_id(db, seed_song_id)
        if not song or not song.artist_id:
            return []
        return await RadioService._upstream_for_artist(db, song.artist_id, limit)

    @staticmethod
    async def _upstream_for_artist(db: AsyncSession, seed_id: str, limit: int) -> List[Song]:
        """
        Best-effort catalogue widening. Gaana keys off its own artist id, so this
        needs a local artist row to supply one -- a name-only seed has nothing to
        query with. Failure here is not an error, it just means the batch comes
        from what we already hold.
        """
        artist = await RadioService._find_artist(db, seed_id)
        key = (artist.external_id or artist.seokey) if artist else None
        if not key:
            return []

        songs: List[Song] = []
        try:
            payload = await catalog_service.gaana.get_top_tracks(str(key), limit=limit)
            raw_tracks = payload.get("tracks") if isinstance(payload, dict) else None
            for raw in raw_tracks or []:
                if isinstance(raw, dict) and raw.get("seokey"):
                    songs.append(await catalog_service.upsert_gaana_song(db, raw))
        except Exception as e:
            logger.warning(f"Radio enrichment for artist {seed_id} failed: {e}")
        return songs


radio_service = RadioService()
