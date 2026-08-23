"""Writes to `recommendation_signals` and refreshes `user_behavior_profiles`.

Both tables were fully modelled and imported by the recommendation service, but
nothing in the codebase ever inserted a row -- production held 0 signals and no
populated behaviour profile. This module is the missing write path.

Why keep them, given `app/ml/user_state.py` recomputes everything from
`listening_history` on demand?

- `recommendation_signals` is an append-only event log at *entity* granularity
  (artist / genre / language / mood / song). `listening_history` records songs, so
  "this user's affinity for Punjabi music" has to be re-derived by joining every
  history row back to `songs` on every request. The signal log also captures
  events with no history row at all -- following an artist, saving an album.
- `user_behavior_profiles` is a materialised summary, so a cold cache does not
  mean a full history aggregation.

Neither is on the critical path: every failure here is logged and swallowed.
A signal that fails to record must not fail the like/play/playlist-add the user
actually asked for.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select, delete, func, desc, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import is_uuid
from app.models.history import ListeningHistory
from app.models.recommendation import RecommendationSignal, UserBehaviorProfile
from app.models.song import Song
from app.ml import config

logger = logging.getLogger("signal_service")

# Signal types accepted. Anything else is a caller bug, so it is logged rather
# than written -- a typo'd signal_type would silently never match a query.
VALID_SIGNAL_TYPES = frozenset(
    {"play", "complete", "like", "unlike", "skip", "repeat", "playlist_add",
     "follow_artist", "save_album", "search"}
)
VALID_ENTITY_TYPES = frozenset({"song", "artist", "genre", "language", "mood", "album", "query"})

# Retention for the event log. Taste has a 60-day half-life, so a signal older
# than this contributes ~1% of its original weight while still costing storage.
SIGNAL_RETENTION_DAYS = 365


class SignalService:
    @staticmethod
    async def record(
        db: AsyncSession,
        user_id: str,
        signals: Sequence[Tuple[str, str, str, float]],
        commit: bool = False,
    ) -> int:
        """Insert (entity_type, entity_id, signal_type, weight) tuples.

        Caller normally leaves `commit=False` so the signals join the same
        transaction as the action that produced them -- a like and its signal
        should not be able to disagree about whether they happened.
        """
        if not is_uuid(user_id) or not signals:
            return 0

        rows: List[RecommendationSignal] = []
        for entity_type, entity_id, signal_type, weight in signals:
            if not entity_id:
                continue
            if signal_type not in VALID_SIGNAL_TYPES or entity_type not in VALID_ENTITY_TYPES:
                logger.warning(
                    "ignoring signal with unknown type: entity_type=%r signal_type=%r",
                    entity_type, signal_type,
                )
                continue
            rows.append(
                RecommendationSignal(
                    user_id=user_id,
                    entity_type=entity_type,
                    entity_id=str(entity_id)[:255],
                    signal_type=signal_type,
                    weight=float(weight),
                )
            )

        if not rows:
            return 0

        try:
            db.add_all(rows)
            await db.flush()
            if commit:
                await db.commit()
        except Exception:
            logger.exception("failed to record %d signals for user %s", len(rows), user_id)
            return 0
        return len(rows)

    @staticmethod
    def _song_signals(
        song: Song,
        signal_type: str,
        weight: float,
    ) -> List[Tuple[str, str, str, float]]:
        """Fan one song event out across the entities it implies.

        A play is evidence about the song, its artist, its genre, its language and
        its mood all at once; storing it only against the song id would mean
        re-joining `songs` to answer any question about artists or genres.
        """
        out: List[Tuple[str, str, str, float]] = [
            ("song", str(song.id), signal_type, weight)
        ]
        if song.artist_name:
            out.append(("artist", song.artist_name.strip().lower(), signal_type, weight))
        if song.genre:
            out.append(("genre", song.genre.strip().lower(), signal_type, weight))
        if song.language:
            out.append(("language", song.language.strip().lower(), signal_type, weight))
        if song.mood:
            out.append(("mood", song.mood.strip().lower(), signal_type, weight))
        return out

    @classmethod
    async def record_play(
        cls,
        db: AsyncSession,
        user_id: str,
        song: Song,
        completion_percentage: float = 0.0,
        skipped: bool = False,
    ) -> int:
        """Signal a play, completion or skip, weighted by how much was heard."""
        if skipped:
            signal_type, weight = "skip", config.SIGNAL_WEIGHTS["skip"]
        elif completion_percentage >= 80.0:
            signal_type = "complete"
            weight = config.SIGNAL_WEIGHTS["complete"] * (0.5 + completion_percentage / 100.0)
        else:
            signal_type = "play"
            weight = config.SIGNAL_WEIGHTS["play"] * (0.5 + max(completion_percentage, 0.0) / 100.0)

        return await cls.record(db, user_id, cls._song_signals(song, signal_type, weight))

    @classmethod
    async def record_song_event(
        cls,
        db: AsyncSession,
        user_id: str,
        song_id: str,
        completion_percentage: float = 0.0,
        skipped: bool = False,
        commit: bool = False,
    ) -> int:
        """`record_play` for callers that hold a song id rather than a Song row.

        The fan-out needs the song's artist, genre, language and mood, so the row
        has to be loaded. Callers that already have it should use `record_play`
        and skip this query.
        """
        if not is_uuid(user_id) or not song_id:
            return 0
        try:
            song = (
                await db.execute(select(Song).where(Song.id == song_id))
            ).scalar_one_or_none()
        except Exception:
            logger.exception("could not load song %s for signal fan-out", song_id)
            return 0
        if song is None:
            return 0

        n = await cls.record_play(
            db, user_id, song,
            completion_percentage=completion_percentage,
            skipped=skipped,
        )
        if n and commit:
            try:
                await db.commit()
            except Exception:
                logger.exception("failed to commit %d signals for user %s", n, user_id)
                await db.rollback()
                return 0
        return n

    @classmethod
    async def record_like(cls, db: AsyncSession, user_id: str, song: Song, liked: bool = True) -> int:
        """Signal a like, or cancel one out with an equal negative on unlike.

        An append-only log cannot delete, so an unlike is recorded as the negation
        of the like. Summing the log then gives the correct current affinity.
        """
        weight = config.SIGNAL_WEIGHTS["like"] if liked else -config.SIGNAL_WEIGHTS["like"]
        return await cls.record(
            db, user_id, cls._song_signals(song, "like" if liked else "unlike", weight)
        )

    @classmethod
    async def record_playlist_add(cls, db: AsyncSession, user_id: str, song: Song) -> int:
        return await cls.record(
            db, user_id,
            cls._song_signals(song, "playlist_add", config.SIGNAL_WEIGHTS["playlist_add"]),
        )

    @classmethod
    async def record_follow_artist(
        cls, db: AsyncSession, user_id: str, artist_id: str, artist_name: str = ""
    ) -> int:
        weight = config.SIGNAL_WEIGHTS["follow_artist"]
        signals = [("artist", str(artist_id), "follow_artist", weight)]
        if artist_name:
            signals.append(("artist", artist_name.strip().lower(), "follow_artist", weight))
        return await cls.record(db, user_id, signals)

    @classmethod
    async def record_save_album(cls, db: AsyncSession, user_id: str, album_id: str) -> int:
        return await cls.record(
            db, user_id,
            [("album", str(album_id), "save_album", config.SIGNAL_WEIGHTS["save_album"])],
        )

    @classmethod
    async def record_search(cls, db: AsyncSession, user_id: str, query: str) -> int:
        if not query or not query.strip():
            return 0
        return await cls.record(
            db, user_id,
            [("query", query.strip().lower()[:255], "search", config.SIGNAL_WEIGHTS["search"])],
        )

    @staticmethod
    async def top_entities(
        db: AsyncSession,
        user_id: str,
        entity_type: str,
        limit: int = 10,
        since_days: Optional[int] = None,
    ) -> List[Tuple[str, float]]:
        """Highest net-weight entities of one type, from the signal log.

        This is what the log buys over `listening_history`: an ordered artist or
        genre affinity list from one indexed aggregate, no join to `songs`.
        """
        if not is_uuid(user_id):
            return []
        stmt = (
            select(
                RecommendationSignal.entity_id,
                func.sum(RecommendationSignal.weight).label("total"),
            )
            .where(
                RecommendationSignal.user_id == user_id,
                RecommendationSignal.entity_type == entity_type,
            )
            .group_by(RecommendationSignal.entity_id)
            .order_by(desc("total"))
            .limit(limit)
        )
        if since_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
            stmt = stmt.where(RecommendationSignal.created_at >= cutoff)

        rows = (await db.execute(stmt)).all()
        return [(str(entity_id), float(total or 0.0)) for entity_id, total in rows if (total or 0) > 0]

    @staticmethod
    async def refresh_behavior_profile(
        db: AsyncSession,
        user_id: str,
        commit: bool = False,
    ) -> Optional[UserBehaviorProfile]:
        """Recompute and upsert the materialised behaviour summary.

        Called after training rather than on every play: it is a summary for fast
        reads and dashboards, not an input the ranker depends on, so staleness
        between training runs is acceptable.
        """
        if not is_uuid(user_id):
            return None

        agg = (
            await db.execute(
                select(
                    func.count().label("plays"),
                    func.avg(ListeningHistory.completion_percentage).label("avg_completion"),
                    func.sum(func.cast(ListeningHistory.skipped, Integer)).label("skips"),
                    func.sum(ListeningHistory.duration_listened).label("total_seconds"),
                    func.count(func.distinct(ListeningHistory.session_id)).label("sessions"),
                ).where(ListeningHistory.user_id == user_id)
            )
        ).one()

        plays = int(agg.plays or 0)
        sessions = int(agg.sessions or 0) or (1 if plays else 0)

        top_artists = [e for e, _ in await SignalService.top_entities(db, user_id, "artist", 10)]
        top_genres = [e for e, _ in await SignalService.top_entities(db, user_id, "genre", 10)]
        top_languages = [e for e, _ in await SignalService.top_entities(db, user_id, "language", 5)]
        top_moods = [e for e, _ in await SignalService.top_entities(db, user_id, "mood", 5)]

        affinity_scores: Dict[str, Any] = {
            "artists": dict(await SignalService.top_entities(db, user_id, "artist", 20)),
            "genres": dict(await SignalService.top_entities(db, user_id, "genre", 20)),
            "languages": dict(await SignalService.top_entities(db, user_id, "language", 10)),
            "moods": dict(await SignalService.top_entities(db, user_id, "mood", 10)),
        }

        existing = (
            await db.execute(
                select(UserBehaviorProfile).where(UserBehaviorProfile.user_id == user_id)
            )
        ).scalar_one_or_none()

        values = {
            "top_artists": top_artists,
            "top_genres": top_genres,
            "top_languages": top_languages,
            "top_moods": top_moods,
            "average_session_minutes": (float(agg.total_seconds or 0.0) / 60.0 / sessions) if sessions else 0.0,
            "completion_rate": float(agg.avg_completion or 0.0) / 100.0,
            "skip_rate": (int(agg.skips or 0) / plays) if plays else 0.0,
            "affinity_scores": affinity_scores,
        }

        try:
            if existing:
                for key, value in values.items():
                    setattr(existing, key, value)
                profile = existing
            else:
                profile = UserBehaviorProfile(user_id=user_id, **values)
                db.add(profile)
            await db.flush()
            if commit:
                await db.commit()
            return profile
        except Exception:
            logger.exception("failed to refresh behavior profile for %s", user_id)
            return None

    @staticmethod
    async def refresh_all_profiles(db: AsyncSession, commit: bool = True) -> int:
        """Refresh every user who has listening history. Called after training."""
        user_ids = (
            await db.execute(select(ListeningHistory.user_id).distinct())
        ).scalars().all()
        n = 0
        for user_id in user_ids:
            if await SignalService.refresh_behavior_profile(db, str(user_id)):
                n += 1
        if commit and n:
            await db.commit()
        return n

    @staticmethod
    async def prune(db: AsyncSession, retention_days: int = SIGNAL_RETENTION_DAYS) -> int:
        """Delete signals past the retention window. Returns rows removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        result = await db.execute(
            delete(RecommendationSignal).where(RecommendationSignal.created_at < cutoff)
        )
        await db.commit()
        return int(result.rowcount or 0)


signal_service = SignalService()
