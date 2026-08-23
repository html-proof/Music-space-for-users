import logging
import random
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy import select, desc, func, not_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Song, LikedSong, Artist
from app.models.history import ListeningHistory
from app.models.user import UserPreferences
from app.models.recommendation import UserBehaviorProfile, RecommendationSignal
from app.schemas.recommendation import RecommendationCategoryResponse, HomeRecommendationsResponse
from app.services.cache_service import cache_service
from app.services.catalog_service import catalog_service
from app.utils.cache_keys import home_recommendations_key
from app.config.settings import settings

logger = logging.getLogger("recommendation_service")


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

    @staticmethod
    async def get_home_recommendations(
        db: AsyncSession,
        user_id: str,
        user_name: str = "Friend"
    ) -> HomeRecommendationsResponse:
        cache_key = home_recommendations_key(user_id)
        cached = await cache_service.get_json(cache_key)
        if cached:
            try:
                response = HomeRecommendationsResponse.model_validate(cached)
                # The greeting depends on the current hour and the caller's name,
                # so it is recomputed rather than served from the cache.
                response.greeting = RecommendationService._greeting(user_name)
                return response
            except Exception as e:
                logger.warning(f"Discarding unusable cached recommendations for {user_id}: {e}")
                await cache_service.delete(cache_key)

        affinities = await RecommendationService.calculate_user_affinities(db, user_id)
        categories: List[RecommendationCategoryResponse] = []

        # 1. Made For You
        made_for_you_songs = []
        if affinities["top_genres"]:
            stmt = select(Song).where(Song.genre.in_(affinities["top_genres"][:3])).order_by(Song.play_count.desc()).limit(10)
            res = await db.execute(stmt)
            made_for_you_songs = list(res.scalars().all())

        if not made_for_you_songs:
            stmt = select(Song).order_by(Song.play_count.desc()).limit(10)
            res = await db.execute(stmt)
            made_for_you_songs = list(res.scalars().all())

        categories.append(RecommendationCategoryResponse(
            id="made_for_you",
            title="Made For You",
            description="Personalized based on your unique listening habits",
            category_type="made_for_you",
            items=made_for_you_songs
        ))

        # 2. Recently Played
        recent_stmt = (
            select(Song)
            .join(ListeningHistory, ListeningHistory.song_id == Song.id)
            .where(ListeningHistory.user_id == user_id)
            .order_by(desc(ListeningHistory.started_at))
            .limit(10)
        )
        recent_res = await db.execute(recent_stmt)
        recent_songs = list(recent_res.scalars().all())
        if recent_songs:
            categories.append(RecommendationCategoryResponse(
                id="recently_played",
                title="Recently Played",
                description="Jump back into your recent favorites",
                category_type="recently_played",
                items=recent_songs
            ))

        # 3. Because You Listened To <Artist>
        if affinities["top_artists"]:
            fav_artist = affinities["top_artists"][0]
            artist_stmt = select(Song).where(Song.artist_name.ilike(f"%{fav_artist}%")).limit(10)
            artist_res = await db.execute(artist_stmt)
            artist_songs = list(artist_res.scalars().all())
            if artist_songs:
                categories.append(RecommendationCategoryResponse(
                    id="because_you_listened_to",
                    title=f"Because You Listened To {fav_artist}",
                    description=f"Similar hits and deeper cuts from {fav_artist}",
                    category_type="because_you_listened_to",
                    items=artist_songs
                ))

        # 4. Your Daily Mix
        daily_stmt = select(Song).order_by(func.random()).limit(10)
        try:
            daily_res = await db.execute(daily_stmt)
            daily_songs = list(daily_res.scalars().all())
        except Exception:
            daily_res = await db.execute(select(Song).limit(10))
            daily_songs = list(daily_res.scalars().all())

        categories.append(RecommendationCategoryResponse(
            id="daily_mix",
            title="Your Daily Mix",
            description="A fresh blend of your favorite genres and new discoveries",
            category_type="daily_mix",
            items=daily_songs
        ))

        # 5. Trending Tracks
        trending_songs = await catalog_service.get_trending(db, "English", limit=10)
        if trending_songs:
            categories.append(RecommendationCategoryResponse(
                id="trending",
                title="Trending Now",
                description="The hottest tracks right now",
                category_type="trending",
                items=trending_songs
            ))

        # 6. New Releases
        new_songs = await catalog_service.get_new_releases(db, "English", limit=10)
        if new_songs:
            categories.append(RecommendationCategoryResponse(
                id="new_releases",
                title="New Releases",
                description="Freshly dropped songs and albums",
                category_type="new_releases",
                items=new_songs
            ))

        # Greeting logic based on hour
        response = HomeRecommendationsResponse(
            greeting=RecommendationService._greeting(user_name),
            categories=categories,
            top_mix=made_for_you_songs[:6]
        )

        try:
            await cache_service.set_json(
                cache_key,
                response.model_dump(mode="json"),
                ttl_seconds=settings.RECOMMENDATION_CACHE_TTL_SECONDS
            )
        except Exception as e:
            logger.warning(f"Failed to cache recommendations for {user_id}: {e}")

        return response

    @staticmethod
    async def invalidate_home_recommendations(user_id: str) -> None:
        """Drop the cached home feed after a signal that would change it."""
        await cache_service.delete(home_recommendations_key(user_id))

    @staticmethod
    async def get_similar_songs(db: AsyncSession, song_id: str, limit: int = 10) -> List[Song]:
        target_song = await catalog_service.get_song_by_id(db, song_id)
        if not target_song:
            return []

        # Find songs by same artist or genre, excluding the target song
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
