import logging
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select, desc, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.history import ListeningHistory, SearchHistory
from app.models.song import Song
from app.services.cache_service import cache_service
from app.services.catalog_queue import catalog_queue
from app.services.signal_service import SignalService
from app.utils.cache_keys import home_recommendations_key
from app.config.settings import settings

logger = logging.getLogger("history_service")


class HistoryService:
    @staticmethod
    async def record_history(
        db: AsyncSession,
        user_id: str,
        song_id: str,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None,
        duration_listened: float = 0.0,
        completion_percentage: float = 0.0,
        skipped: bool = False,
        source: str = "direct"
    ) -> ListeningHistory:
        # Evaluate meaningful listen criteria (e.g. >= 30s or >= 50%)
        is_meaningful = (
            duration_listened >= settings.MIN_LISTEN_SECONDS or
            completion_percentage >= settings.MIN_COMPLETION_PERCENTAGE
        )

        # ListeningHistory.song_id is an FK, and the song may still be queued.
        await catalog_queue.ensure_persisted(db, song_id)

        now = datetime.now(timezone.utc)
        entry = ListeningHistory(
            user_id=user_id,
            song_id=song_id,
            device_id=device_id,
            session_id=session_id,
            started_at=now,
            ended_at=now,
            duration_listened=duration_listened,
            completion_percentage=completion_percentage,
            skipped=skipped or (not is_meaningful),
            source=source
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)

        # Fan the listen out to the entity-level signal log (song / artist /
        # genre / language / mood). Written after the commit above rather than in
        # the same transaction: the history row is the record of truth, and a
        # signal-log failure must not roll back a play the user actually made.
        await SignalService.record_song_event(
            db,
            user_id=user_id,
            song_id=song_id,
            completion_percentage=completion_percentage,
            skipped=entry.skipped,
            commit=True,
        )

        # A new listen changes the affinities the home feed is built from.
        await cache_service.delete(home_recommendations_key(user_id))
        return entry

    @staticmethod
    async def get_history(
        db: AsyncSession,
        user_id: str,
        limit: int = 20,
        offset: int = 0
    ) -> List[ListeningHistory]:
        stmt = (
            select(ListeningHistory)
            .where(ListeningHistory.user_id == user_id)
            .order_by(desc(ListeningHistory.started_at))
            .offset(offset)
            .limit(limit)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_recent_history(
        db: AsyncSession,
        user_id: str,
        limit: int = 10
    ) -> List[ListeningHistory]:
        stmt = (
            select(ListeningHistory)
            .where(ListeningHistory.user_id == user_id)
            .order_by(desc(ListeningHistory.started_at))
            .limit(limit)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def clear_history(db: AsyncSession, user_id: str) -> bool:
        stmt = delete(ListeningHistory).where(ListeningHistory.user_id == user_id)
        await db.execute(stmt)
        await db.commit()
        return True

    @staticmethod
    async def log_search(
        db: AsyncSession,
        user_id: str,
        query: str,
        result_type: str = "all"
    ) -> SearchHistory:
        entry = SearchHistory(
            user_id=user_id,
            query=query.strip(),
            result_type=result_type
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        # A search is weak evidence of intent, but it is evidence: the query
        # signal is what lets a searched-for artist influence the feed before the
        # user has played anything by them.
        await SignalService.record_search(db, user_id, query)
        try:
            await db.commit()
        except Exception:
            logger.exception("failed to commit search signal for %s", user_id)
            await db.rollback()
        return entry

    @staticmethod
    async def get_search_history(
        db: AsyncSession,
        user_id: str,
        limit: int = 20
    ) -> List[SearchHistory]:
        stmt = (
            select(SearchHistory)
            .where(SearchHistory.user_id == user_id)
            .order_by(desc(SearchHistory.timestamp))
            .limit(limit)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def clear_search_history(db: AsyncSession, user_id: str) -> bool:
        stmt = delete(SearchHistory).where(SearchHistory.user_id == user_id)
        await db.execute(stmt)
        await db.commit()
        return True


history_service = HistoryService()
