import logging
from typing import Optional, Dict, Any, List
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import UserPreferences
from app.models.history import ListeningHistory
from app.models.song import Song
from app.models.recommendation import UserBehaviorProfile
from app.schemas.user import UserPreferencesUpdate, UserAnalyticsResponse

logger = logging.getLogger("user_service")


class UserService:
    @staticmethod
    async def get_preferences(db: AsyncSession, user_id: str) -> UserPreferences:
        stmt = select(UserPreferences).where(UserPreferences.user_id == user_id)
        result = await db.execute(stmt)
        pref = result.scalar_one_or_none()
        if not pref:
            pref = UserPreferences(user_id=user_id)
            db.add(pref)
            await db.commit()
            await db.refresh(pref)
        return pref

    @staticmethod
    async def update_preferences(
        db: AsyncSession,
        user_id: str,
        updates: UserPreferencesUpdate
    ) -> UserPreferences:
        pref = await UserService.get_preferences(db, user_id)
        update_data = updates.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if hasattr(pref, key):
                setattr(pref, key, value)
        await db.commit()
        await db.refresh(pref)
        return pref

    @staticmethod
    def _as_counted_list(raw: Optional[List[Any]]) -> List[dict]:
        """
        Normalize a stored behaviour-profile list to [{"name", "count"}, ...].

        Profiles have been written both as bare strings and as dicts, so rows
        already in the database can be either shape; returning a bare string
        here fails UserAnalyticsResponse validation and 500s the endpoint.
        """
        normalized: List[dict] = []
        for item in raw or []:
            if isinstance(item, dict):
                name = item.get("name") or item.get("value") or item.get("id")
                if name is None:
                    continue
                count = item.get("count", item.get("score", 0))
                try:
                    count = int(count)
                except (TypeError, ValueError):
                    count = 0
                normalized.append({"name": str(name), "count": count})
            elif isinstance(item, str):
                normalized.append({"name": item, "count": 0})
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                normalized.append({"name": str(item[0]), "count": int(item[1] or 0)})
        return normalized

    @staticmethod
    async def get_analytics(db: AsyncSession, user_id: str) -> UserAnalyticsResponse:
        # Check if cached behavior profile exists
        profile_stmt = select(UserBehaviorProfile).where(UserBehaviorProfile.user_id == user_id)
        profile_res = await db.execute(profile_stmt)
        profile = profile_res.scalar_one_or_none()

        # Compute dynamic analytics from listening history
        history_stmt = (
            select(ListeningHistory, Song)
            .join(Song, ListeningHistory.song_id == Song.id)
            .where(ListeningHistory.user_id == user_id)
            .order_by(desc(ListeningHistory.started_at))
            .limit(200)
        )
        history_res = await db.execute(history_stmt)
        records = history_res.all()

        total_plays = len(records)
        if total_plays == 0 and profile:
            return UserAnalyticsResponse(
                top_artists=UserService._as_counted_list(profile.top_artists),
                top_genres=UserService._as_counted_list(profile.top_genres),
                top_languages=UserService._as_counted_list(profile.top_languages),
                top_moods=UserService._as_counted_list(profile.top_moods),
                average_session_minutes=profile.average_session_minutes or 0.0,
                completion_rate=profile.completion_rate or 0.0,
                skip_rate=profile.skip_rate or 0.0
            )

        artist_counts: Dict[str, int] = {}
        genre_counts: Dict[str, int] = {}
        language_counts: Dict[str, int] = {}
        mood_counts: Dict[str, int] = {}
        total_completion = 0.0
        skips = 0

        for hist, song in records:
            if song.artist_name:
                artist_counts[song.artist_name] = artist_counts.get(song.artist_name, 0) + 1
            if song.genre:
                genre_counts[song.genre] = genre_counts.get(song.genre, 0) + 1
            if song.language:
                language_counts[song.language] = language_counts.get(song.language, 0) + 1
            if song.mood:
                mood_counts[song.mood] = mood_counts.get(song.mood, 0) + 1
            total_completion += hist.completion_percentage
            if hist.skipped:
                skips += 1

        top_artists = [{"name": k, "count": v} for k, v in sorted(artist_counts.items(), key=lambda x: x[1], reverse=True)[:5]]
        top_genres = [{"name": k, "count": v} for k, v in sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:5]]
        top_languages = [{"name": k, "count": v} for k, v in sorted(language_counts.items(), key=lambda x: x[1], reverse=True)[:5]]
        top_moods = [{"name": k, "count": v} for k, v in sorted(mood_counts.items(), key=lambda x: x[1], reverse=True)[:5]]
        avg_completion = round(total_completion / max(total_plays, 1), 2)
        skip_rate = round(skips / max(total_plays, 1), 2)

        return UserAnalyticsResponse(
            top_artists=top_artists,
            top_genres=top_genres,
            top_languages=top_languages,
            top_moods=top_moods,
            average_session_minutes=round(total_plays * 3.5, 1),
            completion_rate=avg_completion,
            skip_rate=skip_rate
        )


user_service = UserService()
