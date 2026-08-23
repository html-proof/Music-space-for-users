"""Home feed shelves, similar songs, and mood mixes.

Rebuilt on the `app/ml` pipeline: retrieval -> features -> ranking ->
diversification. The pre-ML heuristics are preserved verbatim in the
`_heuristic_*` methods and are what runs when `ML_ENABLED=False` or when the ML
path raises -- the feed is the app's landing screen, so it must render something
even if the model layer is broken.

Public signatures and the `HomeRecommendationsResponse` shape are unchanged; only
the ordering of the songs inside them is different.
"""
import logging
import random
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import and_, select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.base import is_uuid
from app.models.history import ListeningHistory
from app.models.song import Song, LikedSong, Artist
from app.models.user import UserPreferences
from app.models.recommendation import UserBehaviorProfile, RecommendationSignal
from app.schemas.recommendation import RecommendationCategoryResponse, HomeRecommendationsResponse
from app.services.cache_service import cache_service
from app.services.catalog_service import catalog_service
from app.utils.cache_keys import home_recommendations_key, similar_songs_key

logger = logging.getLogger("recommendation_service")

SHELF_SIZE = 10


class RecommendationService:
    @staticmethod
    async def calculate_user_affinities(db: AsyncSession, user_id: str) -> Dict[str, Any]:
        """Calculates dynamic user preferences and affinities from listening history and likes."""
        history_stmt = (
            select(ListeningHistory, Song)
            .join(Song, ListeningHistory.song_id == Song.id)
            .where(ListeningHistory.user_id == user_id)
            .order_by(desc(ListeningHistory.started_at))
            .limit(100)
        )
        history_res = await db.execute(history_stmt)
        history_items = history_res.all()

        liked_stmt = (
            select(Song)
            .join(LikedSong, LikedSong.song_id == Song.id)
            .where(LikedSong.user_id == user_id)
            .limit(50)
        )
        liked_res = await db.execute(liked_stmt)
        liked_songs = list(liked_res.scalars().all())

        artist_scores: Dict[str, float] = {}
        genre_scores: Dict[str, float] = {}
        language_scores: Dict[str, float] = {}
        mood_scores: Dict[str, float] = {}
        recent_song_ids = set()

        for hist, song in history_items:
            recent_song_ids.add(song.id)
            weight = 1.0
            if hist.completion_percentage >= 80.0:
                weight += 1.5
            elif hist.skipped:
                weight -= 1.0

            if song.artist_name:
                artist_scores[song.artist_name] = artist_scores.get(song.artist_name, 0.0) + weight
            if song.genre:
                genre_scores[song.genre] = genre_scores.get(song.genre, 0.0) + weight
            if song.language:
                language_scores[song.language] = language_scores.get(song.language, 0.0) + weight
            if song.mood:
                mood_scores[song.mood] = mood_scores.get(song.mood, 0.0) + weight

        for song in liked_songs:
            if song.artist_name:
                artist_scores[song.artist_name] = artist_scores.get(song.artist_name, 0.0) + 3.0
            if song.genre:
                genre_scores[song.genre] = genre_scores.get(song.genre, 0.0) + 2.0

        # Seed with explicitly declared onboarding preferences too, not just
        # observed behaviour. A freshly onboarded user has no listening
        # history or likes yet, so without this every score dict above is
        # empty and the home feed falls back to a fully generic, unfiltered
        # list -- silently ignoring the language and artists they just chose.
        if is_uuid(user_id):
            pref = (
                await db.execute(select(UserPreferences).where(UserPreferences.user_id == user_id))
            ).scalar_one_or_none()
            if pref:
                for lang in pref.preferred_languages or []:
                    language_scores[lang] = language_scores.get(lang, 0.0) + 1.0
                for artist in pref.favorite_artists or []:
                    artist_scores[artist] = artist_scores.get(artist, 0.0) + 1.0
                for genre in pref.favorite_genres or []:
                    genre_scores[genre] = genre_scores.get(genre, 0.0) + 1.0

        return {
            "top_artists": [k for k, v in sorted(artist_scores.items(), key=lambda x: x[1], reverse=True) if v > 0],
            "top_genres": [k for k, v in sorted(genre_scores.items(), key=lambda x: x[1], reverse=True) if v > 0],
            "top_languages": [k for k, v in sorted(language_scores.items(), key=lambda x: x[1], reverse=True) if v > 0],
            "top_moods": [k for k, v in sorted(mood_scores.items(), key=lambda x: x[1], reverse=True) if v > 0],
            "recent_song_ids": list(recent_song_ids)
        }

    @staticmethod
    def _greeting(user_name: str) -> str:
        hour = datetime.now().hour
        if 5 <= hour < 12:
            return f"Good morning, {user_name}"
        if 12 <= hour < 17:
            return f"Good afternoon, {user_name}"
        return f"Good evening, {user_name}"

    # ------------------------------------------------------------------ home feed

    @staticmethod
    async def get_home_recommendations(
        db: AsyncSession,
        user_id: str,
        user_name: str = "Friend"
    ) -> HomeRecommendationsResponse:
        """Personalized shelves are cached; catalog shelves (Trending, New
        Releases) are not.

        Ranking a user's taste against the candidate pool is the expensive
        part of this response, so it is worth caching for
        RECOMMENDATION_CACHE_TTL_SECONDS. Trending/New Releases are cheap by
        comparison (catalog_service already caches the underlying Gaana call
        for 30 minutes) and are what every user actually expects to look
        current -- baking them into an hour-long cached response meant a
        chart that moved on Gaana could sit stale here for up to an hour
        after. They are now fetched fresh on every request, on both the
        cache-hit and cache-miss paths below.
        """
        cache_key = home_recommendations_key(user_id)
        cached = await cache_service.get_json(cache_key)
        if cached:
            try:
                response = HomeRecommendationsResponse.model_validate(cached)
                # The greeting depends on the current hour and the caller's name,
                # so it is recomputed rather than served from the cache.
                response.greeting = RecommendationService._greeting(user_name)
                await RecommendationService._append_catalog_shelves(db, response.categories, user_id)
                return response
            except Exception as e:
                logger.warning(f"Discarding unusable cached recommendations for {user_id}: {e}")
                await cache_service.delete(cache_key)

        categories: List[RecommendationCategoryResponse] = []
        top_mix: List[Song] = []

        if settings.ML_ENABLED:
            try:
                categories, top_mix = await RecommendationService._ml_shelves(db, user_id)
            except Exception:
                # The home feed is the landing screen. A broken model layer must
                # degrade to the old heuristics, not to an error page.
                logger.exception("ML home feed failed for %s; falling back to heuristics", user_id)
                categories, top_mix = [], []

        if not categories:
            categories, top_mix = await RecommendationService._heuristic_shelves(db, user_id)

        try:
            await cache_service.set_json(
                cache_key,
                HomeRecommendationsResponse(
                    greeting=RecommendationService._greeting(user_name),
                    categories=categories,
                    top_mix=top_mix[:6],
                ).model_dump(mode="json"),
                ttl_seconds=settings.RECOMMENDATION_CACHE_TTL_SECONDS
            )
        except Exception as e:
            logger.warning(f"Failed to cache recommendations for {user_id}: {e}")

        # Appended after caching, and to a fresh list each time -- these must
        # never end up baked into the cached personalized payload above.
        await RecommendationService._append_catalog_shelves(db, categories, user_id)

        return HomeRecommendationsResponse(
            greeting=RecommendationService._greeting(user_name),
            categories=categories,
            top_mix=top_mix[:6],
        )

    @staticmethod
    async def _ml_shelves(
        db: AsyncSession,
        user_id: str,
    ) -> Tuple[List[RecommendationCategoryResponse], List[Song]]:
        """Build the personalized shelves from one ranked candidate pool.

        Retrieval and ranking run *once*; the shelves are then carved out of that
        single ranked list by filtering on different criteria. Generating
        candidates per shelf would multiply the query cost by the shelf count for
        results that overlap almost entirely.
        """
        from app.ml import candidates as ml_candidates
        from app.ml import diversify, ranker as ml_ranker
        from app.ml.user_state import build_user_state, load_song_stats, max_play_count

        state = await build_user_state(db, user_id)
        pool = await ml_candidates.generate(db, state, limit=settings.ML_CANDIDATE_LIMIT)
        if len(pool) == 0:
            return [], []

        stats = await load_song_stats(db, list(pool.songs.keys()))
        max_play = await max_play_count(db)
        active_ranker = await ml_ranker.load_ranker(db)

        scored = active_ranker.rank(
            pool.song_list(),
            state,
            sources=pool.source_map(),
            cf_scores=pool.cf_scores,
            stats=stats,
            max_play_count=max_play,
            vectors=pool.vectors,
        )

        played = set(state.play_counts.keys())
        # Stable per-user exploration: two requests in the same hour return the
        # same shelf, so a user does not watch the feed reshuffle under them.
        rng = random.Random(f"{user_id}:{datetime.now().strftime('%Y%m%d%H')}")
        used: Set[str] = set()

        def shelf(
            items: Sequence[Any],
            *,
            exclude_used: bool = True,
            size: int = SHELF_SIZE,
            explore: Optional[float] = None,
        ) -> List[Song]:
            picked = diversify.finalize(
                items,
                limit=size,
                played_song_ids=played,
                exclude_ids=set(used) if exclude_used else set(),
                exploration_rate=settings.ML_EXPLORATION_RATE if explore is None else explore,
                rng=rng,
            )
            if exclude_used:
                used.update(s.song_id for s in picked)
            return [s.song for s in picked]

        categories: List[RecommendationCategoryResponse] = []

        # Top Mix -- the highest-scoring songs overall, no exclusions, no
        # exploration. This is the model's unhedged best guess.
        top_mix = shelf(scored, exclude_used=False, size=SHELF_SIZE, explore=0.0)

        made_for_you = shelf(scored)
        if made_for_you:
            categories.append(RecommendationCategoryResponse(
                id="made_for_you",
                title="Made For You",
                description=(
                    "Ranked for you by your listening history"
                    if active_ranker.is_learned
                    else "Personalized based on your unique listening habits"
                ),
                category_type="made_for_you",
                items=made_for_you,
            ))

        # Discover Weekly -- restricted to songs the user has never played, and
        # given a wider MMR window because discovery is the point.
        unheard = [s for s in scored if s.song_id not in played]
        discover = diversify.finalize(
            unheard, limit=SHELF_SIZE, played_song_ids=played,
            exclude_ids=set(used), mmr_lambda=0.55, rng=rng,
        )
        if discover:
            used.update(s.song_id for s in discover)
            categories.append(RecommendationCategoryResponse(
                id="discover_weekly",
                title="Discover Weekly",
                description="Songs you have not heard yet, picked from your taste profile",
                category_type="discover_weekly",
                items=[s.song for s in discover],
            ))

        # Because You Listened To <artist>
        top_artist = max(state.artist_affinity.items(), key=lambda p: p[1])[0] if state.artist_affinity else None
        if top_artist:
            display = await RecommendationService._display_artist_name(db, top_artist)
            related = [
                s for s in scored
                if (getattr(s.song, "artist_name", "") or "").strip().lower() != top_artist
                or s.song_id not in played
            ]
            items = diversify.finalize(
                related, limit=SHELF_SIZE, played_song_ids=played,
                exclude_ids=set(), max_per_artist=4, rng=rng,
            )
            if items:
                categories.append(RecommendationCategoryResponse(
                    id="because_you_listened_to",
                    title=f"Because You Listened To {display}",
                    description=f"Similar hits and deeper cuts around {display}",
                    category_type="because_you_listened_to",
                    items=[s.song for s in items],
                ))

        # Daily Mix -- was ORDER BY random(). Now a deterministic per-day
        # shuffle of the ranked pool: still varied day to day, but every song in
        # it was scored for this user rather than drawn from the whole catalog.
        daily_pool = list(scored[: SHELF_SIZE * 6])
        random.Random(f"{user_id}:{datetime.now().strftime('%Y%m%d')}").shuffle(daily_pool)
        daily = shelf(daily_pool, exclude_used=False, explore=0.0)
        if daily:
            categories.append(RecommendationCategoryResponse(
                id="daily_mix",
                title="Your Daily Mix",
                description="A fresh blend of your favorite genres and new discoveries",
                category_type="daily_mix",
                items=daily,
            ))

        recent = await RecommendationService._recently_played(db, user_id)
        if recent:
            categories.append(RecommendationCategoryResponse(
                id="recently_played",
                title="Recently Played",
                description="Jump back into your recent favorites",
                category_type="recently_played",
                items=recent,
            ))

        return categories, top_mix

    @staticmethod
    async def _heuristic_shelves(
        db: AsyncSession,
        user_id: str,
    ) -> Tuple[List[RecommendationCategoryResponse], List[Song]]:
        """The pre-ML shelves, kept intact as the ML_ENABLED=False path."""
        affinities = await RecommendationService.calculate_user_affinities(db, user_id)
        categories: List[RecommendationCategoryResponse] = []

        # top_languages was computed above but never consulted here -- a
        # declared/observed language preference is at least as strong a
        # signal as genre, so it filters made_for_you the same way.
        made_for_you_songs: List[Song] = []
        filters = []
        if affinities["top_genres"]:
            filters.append(Song.genre.in_(affinities["top_genres"][:3]))
        if affinities["top_languages"]:
            filters.append(Song.language.in_(affinities["top_languages"][:3]))

        if filters:
            stmt = select(Song).where(and_(*filters)).order_by(Song.play_count.desc()).limit(SHELF_SIZE)
            made_for_you_songs = list((await db.execute(stmt)).scalars().all())
            if not made_for_you_songs and len(filters) > 1:
                # Genre + language together may be too narrow on a small
                # catalog -- relax to language alone, the stronger signal.
                stmt = (
                    select(Song)
                    .where(Song.language.in_(affinities["top_languages"][:3]))
                    .order_by(Song.play_count.desc())
                    .limit(SHELF_SIZE)
                )
                made_for_you_songs = list((await db.execute(stmt)).scalars().all())

        if not made_for_you_songs:
            stmt = select(Song).order_by(Song.play_count.desc()).limit(SHELF_SIZE)
            made_for_you_songs = list((await db.execute(stmt)).scalars().all())

        categories.append(RecommendationCategoryResponse(
            id="made_for_you",
            title="Made For You",
            description="Personalized based on your unique listening habits",
            category_type="made_for_you",
            items=made_for_you_songs,
        ))

        recent_songs = await RecommendationService._recently_played(db, user_id)
        if recent_songs:
            categories.append(RecommendationCategoryResponse(
                id="recently_played",
                title="Recently Played",
                description="Jump back into your recent favorites",
                category_type="recently_played",
                items=recent_songs,
            ))

        if affinities["top_artists"]:
            fav_artist = affinities["top_artists"][0]
            artist_stmt = select(Song).where(Song.artist_name.ilike(f"%{fav_artist}%")).limit(SHELF_SIZE)
            artist_songs = list((await db.execute(artist_stmt)).scalars().all())
            if artist_songs:
                categories.append(RecommendationCategoryResponse(
                    id="because_you_listened_to",
                    title=f"Because You Listened To {fav_artist}",
                    description=f"Similar hits and deeper cuts from {fav_artist}",
                    category_type="because_you_listened_to",
                    items=artist_songs,
                ))

        try:
            daily_songs = list(
                (await db.execute(select(Song).order_by(func.random()).limit(SHELF_SIZE)))
                .scalars().all()
            )
        except Exception:
            daily_songs = list((await db.execute(select(Song).limit(SHELF_SIZE))).scalars().all())

        categories.append(RecommendationCategoryResponse(
            id="daily_mix",
            title="Your Daily Mix",
            description="A fresh blend of your favorite genres and new discoveries",
            category_type="daily_mix",
            items=daily_songs,
        ))

        return categories, made_for_you_songs

    @staticmethod
    async def _recently_played(db: AsyncSession, user_id: str, limit: int = SHELF_SIZE) -> List[Song]:
        if not is_uuid(user_id):
            return []
        stmt = (
            select(Song)
            .join(ListeningHistory, ListeningHistory.song_id == Song.id)
            .where(ListeningHistory.user_id == user_id)
            .order_by(desc(ListeningHistory.started_at))
            .limit(limit)
        )
        seen: Set[str] = set()
        out: List[Song] = []
        for song in (await db.execute(stmt)).scalars().all():
            if str(song.id) in seen:
                continue
            seen.add(str(song.id))
            out.append(song)
        return out

    @staticmethod
    async def _display_artist_name(db: AsyncSession, lowered: str) -> str:
        """Recover an artist's display casing from a lowercased affinity key.

        Affinity dicts are lowercased so "A.R. Rahman" and "a.r. rahman" are the
        same key, but a shelf title has to show the original.
        """
        res = await db.execute(
            select(Song.artist_name).where(func.lower(Song.artist_name) == lowered).limit(1)
        )
        return res.scalar_one_or_none() or lowered.title()

    @staticmethod
    async def _append_catalog_shelves(
        db: AsyncSession,
        categories: List[RecommendationCategoryResponse],
        user_id: str,
    ) -> None:
        """Trending and New Releases, in the user's preferred language when known.

        Idempotent by construction: drops any existing trending/new_releases
        entries before appending fresh ones, so a cached response saved by an
        older version of this service (from before catalog shelves were
        excluded from the cache) never ends up with the pair duplicated
        during a deploy's cache-TTL rollover window.
        """
        categories[:] = [
            c for c in categories if c.category_type not in ("trending", "new_releases")
        ]
        language = "English"
        if is_uuid(user_id):
            res = await db.execute(
                select(UserPreferences.preferred_languages).where(UserPreferences.user_id == user_id)
            )
            langs = res.scalar_one_or_none()
            if langs:
                language = str(langs[0])

        try:
            trending_songs = await catalog_service.get_trending(db, language, limit=SHELF_SIZE)
        except Exception:
            logger.warning("trending shelf unavailable", exc_info=True)
            trending_songs = []
        if trending_songs:
            categories.append(RecommendationCategoryResponse(
                id="trending",
                title="Trending Now",
                description="The hottest tracks right now",
                category_type="trending",
                items=trending_songs,
            ))

        try:
            new_songs = await catalog_service.get_new_releases(db, language, limit=SHELF_SIZE)
        except Exception:
            logger.warning("new releases shelf unavailable", exc_info=True)
            new_songs = []
        if new_songs:
            categories.append(RecommendationCategoryResponse(
                id="new_releases",
                title="New Releases",
                description="Freshly dropped songs and albums",
                category_type="new_releases",
                items=new_songs,
            ))

    @staticmethod
    async def invalidate_home_recommendations(user_id: str) -> None:
        """Drop the cached home feed after a signal that would change it."""
        await cache_service.delete(home_recommendations_key(user_id))

    # -------------------------------------------------------------- similar songs

    @staticmethod
    async def get_similar_songs(db: AsyncSession, song_id: str, limit: int = 10) -> List[Song]:
        """Songs similar to `song_id`: content kNN blended with item-item CF.

        Item-to-item and therefore user-independent, which is what makes it
        cacheable across users and usable for an anonymous listener.
        """
        target_song = await catalog_service.get_song_by_id(db, song_id)
        if not target_song:
            return []

        if not settings.ML_ENABLED:
            return await RecommendationService._heuristic_similar(db, target_song, limit)

        cache_key = similar_songs_key(str(target_song.id), limit)
        cached = await cache_service.get_json(cache_key)
        if cached:
            rows = (await db.execute(select(Song).where(Song.id.in_(cached)))).scalars().all()
            by_id = {str(s.id): s for s in rows}
            ordered = [by_id[sid] for sid in cached if sid in by_id]
            if ordered:
                return ordered

        try:
            from app.ml import candidates as ml_candidates
            from app.ml import diversify, ranker as ml_ranker
            from app.ml.features import UserState, song_vector
            from app.ml.user_state import load_song_stats, max_play_count

            pool = await ml_candidates.for_seed_song(db, target_song, limit=max(limit * 20, 100))
            if len(pool) == 0:
                return await RecommendationService._heuristic_similar(db, target_song, limit)

            # A synthetic single-song "user": the seed's own content vector as the
            # taste vector. This reuses the full ranker -- including popularity and
            # global completion rate -- for an item-to-item query, instead of
            # ranking on raw cosine alone.
            seed_state = UserState(
                user_id="",
                as_of=datetime.now().astimezone(),
                taste_vector=song_vector(target_song),
                preferred_languages={(target_song.language or "").strip().lower()} - {""},
            )
            stats = await load_song_stats(db, list(pool.songs.keys()))
            active_ranker = await ml_ranker.load_ranker(db)
            scored = active_ranker.rank(
                pool.song_list(), seed_state,
                sources=pool.source_map(), cf_scores=pool.cf_scores,
                stats=stats, max_play_count=await max_play_count(db), vectors=pool.vectors,
            )
            picked = diversify.finalize(
                scored, limit=limit, exclude_ids={str(target_song.id)},
                max_per_artist=3, exploration_rate=0.0,
            )
            songs = [s.song for s in picked]
        except Exception:
            logger.exception("ML similar-songs failed for %s; falling back", song_id)
            return await RecommendationService._heuristic_similar(db, target_song, limit)

        if not songs:
            return await RecommendationService._heuristic_similar(db, target_song, limit)

        try:
            await cache_service.set_json(
                cache_key, [str(s.id) for s in songs],
                ttl_seconds=settings.RECOMMENDATION_CACHE_TTL_SECONDS,
            )
        except Exception:
            logger.debug("failed to cache similar songs for %s", song_id, exc_info=True)
        return songs

    @staticmethod
    async def _heuristic_similar(db: AsyncSession, target_song: Song, limit: int) -> List[Song]:
        """Same artist or genre, by play count -- the pre-ML behaviour."""
        stmt = (
            select(Song)
            .where(
                Song.id != target_song.id,
                (Song.artist_name == target_song.artist_name) | (Song.genre == target_song.genre)
            )
            .order_by(Song.play_count.desc())
            .limit(limit)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_mood_mix(db: AsyncSession, mood: str, limit: int = 20) -> List[Song]:
        stmt = select(Song).where(Song.mood.ilike(f"%{mood}%")).order_by(Song.play_count.desc()).limit(limit)
        res = await db.execute(stmt)
        songs = list(res.scalars().all())
        if not songs:
            # Fallback to general songs
            res = await db.execute(select(Song).limit(limit))
            songs = list(res.scalars().all())
        return songs


recommendation_service = RecommendationService()
