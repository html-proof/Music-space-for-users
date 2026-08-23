"""
Listening statistics -- the "top tracks / top artists / your year" surface.

Every figure is aggregated in SQL from `listening_history` at request time. No
precomputed rollup table and no nightly job, because the free plan has neither
background workers nor cron. The aggregates are small (grouped, limited, and
bounded by a time window) and `ix_history_user_started` covers the filter, so
computing them per request is cheaper than maintaining a rollup would be.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.history import ListeningHistory
from app.models.song import Song

logger = logging.getLogger("stats_service")

# Spotify's own windows, so a client can mirror the wording.
RANGE_DAYS: Dict[str, Optional[int]] = {"4weeks": 28, "6months": 182, "all": None}
DEFAULT_RANGE = "4weeks"
MAX_LIMIT = 50


class StatsService:
    @staticmethod
    def since_for(range_key: str) -> Optional[datetime]:
        """Start of the window, or None for all-time."""
        days = RANGE_DAYS.get(range_key, RANGE_DAYS[DEFAULT_RANGE])
        if days is None:
            return None
        return datetime.now(timezone.utc) - timedelta(days=days)

    @staticmethod
    def _history_join(*columns):
        """
        Aggregate over a user's history joined to the played song.

        The FROM has to be stated explicitly: with Song in the columns clause,
        SQLAlchemy would otherwise pick it as the left side and try to join it
        to itself.
        """
        return (
            select(*columns)
            .select_from(ListeningHistory)
            .join(Song, Song.id == ListeningHistory.song_id)
        )

    @staticmethod
    def _scoped(stmt, user_id: str, since: Optional[datetime]):
        stmt = stmt.where(ListeningHistory.user_id == user_id)
        if since is not None:
            stmt = stmt.where(ListeningHistory.started_at >= since)
        return stmt

    @staticmethod
    async def top_tracks(
        db: AsyncSession,
        user_id: str,
        range_key: str = DEFAULT_RANGE,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Most-played tracks in the window, ranked by play count then by time
        spent -- so a track played twice in full outranks one started twice and
        abandoned.
        """
        since = StatsService.since_for(range_key)
        # Labels must not collide with a selected column: `play_count` is also a
        # real Song column, and PostgreSQL rejects the resulting ORDER BY as
        # ambiguous where SQLite silently picks one.
        plays = func.count(ListeningHistory.id).label("stat_plays")
        seconds = func.coalesce(func.sum(ListeningHistory.duration_listened), 0.0).label("stat_seconds")

        stmt = (
            StatsService._history_join(Song, plays, seconds)
            .group_by(Song.id)
            .order_by(plays.desc(), seconds.desc())
            .limit(min(limit, MAX_LIMIT))
        )
        res = await db.execute(StatsService._scoped(stmt, user_id, since))

        return [
            {
                "rank": rank,
                "song": song,
                "play_count": int(count or 0),
                "seconds_listened": round(float(total or 0.0), 2),
            }
            for rank, (song, count, total) in enumerate(res.all(), start=1)
        ]

    @staticmethod
    async def top_artists(
        db: AsyncSession,
        user_id: str,
        range_key: str = DEFAULT_RANGE,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Grouped on `Song.artist_name` rather than the artist row: history rows
        reference songs, and a song's artist_id can be null while its name is
        always populated. Names also collapse the same artist across duplicate
        rows created by different upstream ids.
        """
        since = StatsService.since_for(range_key)
        plays = func.count(ListeningHistory.id).label("stat_plays")
        seconds = func.coalesce(func.sum(ListeningHistory.duration_listened), 0.0).label("stat_seconds")
        tracks = func.count(func.distinct(Song.id)).label("stat_tracks")

        stmt = (
            StatsService._history_join(Song.artist_name, plays, seconds, tracks)
            .where(Song.artist_name != "")
            .group_by(Song.artist_name)
            .order_by(plays.desc(), seconds.desc())
            .limit(min(limit, MAX_LIMIT))
        )
        res = await db.execute(StatsService._scoped(stmt, user_id, since))

        return [
            {
                "rank": rank,
                "artist_name": name,
                "play_count": int(count or 0),
                "seconds_listened": round(float(total or 0.0), 2),
                "track_count": int(distinct_tracks or 0),
            }
            for rank, (name, count, total, distinct_tracks) in enumerate(res.all(), start=1)
        ]

    @staticmethod
    async def _top_column(
        db: AsyncSession,
        user_id: str,
        since: Optional[datetime],
        column: ColumnElement,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Play counts grouped by one Song attribute (genre, language, mood)."""
        plays = func.count(ListeningHistory.id).label("stat_plays")
        stmt = (
            StatsService._history_join(column, plays)
            .where(column.is_not(None), column != "")
            .group_by(column)
            .order_by(plays.desc())
            .limit(limit)
        )
        res = await db.execute(StatsService._scoped(stmt, user_id, since))
        return [{"name": name, "play_count": int(count or 0)} for name, count in res.all()]

    @staticmethod
    async def summary(
        db: AsyncSession,
        user_id: str,
        range_key: str = DEFAULT_RANGE
    ) -> Dict[str, Any]:
        """Totals and taste breakdown for the window."""
        since = StatsService.since_for(range_key)

        # Totals need no join, so this one is a plain select over history.
        totals_stmt = select(
            func.count(ListeningHistory.id),
            func.coalesce(func.sum(ListeningHistory.duration_listened), 0.0),
            func.count(func.distinct(ListeningHistory.song_id)),
            # CAST rather than a FILTER clause: booleans sum this way on both
            # PostgreSQL and SQLite.
            func.coalesce(func.sum(func.cast(ListeningHistory.skipped, Integer)), 0),
        )
        res = await db.execute(StatsService._scoped(totals_stmt, user_id, since))
        plays, seconds, distinct_songs, skips = res.one()

        plays = int(plays or 0)
        seconds = float(seconds or 0.0)
        skips = int(skips or 0)

        artists_stmt = StatsService._history_join(
            func.count(func.distinct(Song.artist_name))
        ).where(Song.artist_name != "")
        res = await db.execute(StatsService._scoped(artists_stmt, user_id, since))
        distinct_artists = int(res.scalar() or 0)

        return {
            "range": range_key,
            "since": since.isoformat() if since else None,
            "total_plays": plays,
            "total_seconds_listened": round(seconds, 2),
            "total_minutes_listened": round(seconds / 60.0, 2),
            "distinct_songs": int(distinct_songs or 0),
            "distinct_artists": distinct_artists,
            "skipped_plays": skips,
            # Guard the division: a user with no history yet must get 0, not a
            # 500 from the stats page.
            "skip_rate": round(skips / plays, 4) if plays else 0.0,
            "average_seconds_per_play": round(seconds / plays, 2) if plays else 0.0,
            "top_genres": await StatsService._top_column(db, user_id, since, Song.genre),
            "top_languages": await StatsService._top_column(db, user_id, since, Song.language),
            "top_moods": await StatsService._top_column(db, user_id, since, Song.mood),
        }


stats_service = StatsService()
