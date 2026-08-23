import logging
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select, and_, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.download import Download, DOWNLOAD_QUALITIES, DOWNLOAD_STATUSES
from app.models.song import Song
from app.services.catalog_service import catalog_service

logger = logging.getLogger("download_service")


class DownloadService:
    @staticmethod
    def resolve_audio_url(song: Song, quality: str) -> Optional[str]:
        urls = song.stream_urls or {}
        return urls.get(quality) or song.audio_url

    @staticmethod
    async def request_download(
        db: AsyncSession,
        user_id: str,
        song_id: str,
        device_id: str,
        quality: str,
    ) -> Optional[Download]:
        if quality not in DOWNLOAD_QUALITIES:
            quality = "high_quality"

        song = await catalog_service.get_song_by_id(db, song_id)
        if not song:
            return None

        stmt = select(Download).where(
            and_(
                Download.user_id == user_id,
                Download.song_id == song.id,
                Download.device_id == device_id,
            )
        )
        res = await db.execute(stmt)
        download = res.scalar_one_or_none()

        if download:
            # Re-requesting (including retrying a failed download) restarts it.
            download.status = "queued"
            download.quality = quality
            download.progress_percent = 0
            download.file_size_bytes = None
            download.error_message = None
            download.requested_at = datetime.now(timezone.utc)
            download.completed_at = None
        else:
            download = Download(
                user_id=user_id,
                song_id=song.id,
                device_id=device_id,
                status="queued",
                quality=quality,
            )
            db.add(download)

        await db.commit()
        return await DownloadService._reload(db, download.id)

    @staticmethod
    async def _reload(db: AsyncSession, download_id: str) -> Optional[Download]:
        """Re-select so the lazy="selectin" `song` relationship loads as part of
        this query, rather than being touched lazily on an async session (which
        raises outside of a query context)."""
        stmt = select(Download).where(Download.id == download_id).execution_options(populate_existing=True)
        res = await db.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def update_progress(
        db: AsyncSession,
        user_id: str,
        download_id: str,
        status: Optional[str],
        progress_percent: Optional[int],
        file_size_bytes: Optional[int],
        error_message: Optional[str],
    ) -> Optional[Download]:
        stmt = select(Download).where(and_(Download.id == download_id, Download.user_id == user_id))
        res = await db.execute(stmt)
        download = res.scalar_one_or_none()
        if not download:
            return None

        if status is not None:
            if status not in DOWNLOAD_STATUSES:
                return None
            download.status = status
            if status == "completed":
                download.progress_percent = 100
                download.completed_at = datetime.now(timezone.utc)
            elif status == "queued":
                download.error_message = None
        if progress_percent is not None:
            download.progress_percent = progress_percent
        if file_size_bytes is not None:
            download.file_size_bytes = file_size_bytes
        if error_message is not None:
            download.error_message = error_message

        await db.commit()
        return await DownloadService._reload(db, download.id)

    @staticmethod
    async def list_downloads(
        db: AsyncSession,
        user_id: str,
        device_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Download]:
        conditions = [Download.user_id == user_id]
        if device_id:
            conditions.append(Download.device_id == device_id)
        if status:
            conditions.append(Download.status == status)

        stmt = (
            select(Download)
            .where(and_(*conditions))
            .order_by(Download.requested_at.desc())
            .offset(offset)
            .limit(limit)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_storage_summary(db: AsyncSession, user_id: str, device_id: Optional[str] = None) -> dict:
        conditions = [Download.user_id == user_id]
        if device_id:
            conditions.append(Download.device_id == device_id)

        stmt = select(Download.status, func.count(Download.id), func.coalesce(func.sum(Download.file_size_bytes), 0)).where(
            and_(*conditions)
        ).group_by(Download.status)
        res = await db.execute(stmt)
        rows = res.all()

        by_status = {row[0]: row[1] for row in rows}
        total_downloads = sum(by_status.values())
        completed_downloads = by_status.get("completed", 0)
        total_bytes = sum(row[2] for row in rows)

        return {
            "total_downloads": total_downloads,
            "completed_downloads": completed_downloads,
            "total_bytes": int(total_bytes),
            "by_status": by_status,
        }

    @staticmethod
    async def delete_download(db: AsyncSession, user_id: str, download_id: str) -> bool:
        stmt = delete(Download).where(and_(Download.id == download_id, Download.user_id == user_id))
        res = await db.execute(stmt)
        await db.commit()
        return res.rowcount > 0

    @staticmethod
    async def delete_all_downloads(db: AsyncSession, user_id: str, device_id: Optional[str] = None) -> int:
        conditions = [Download.user_id == user_id]
        if device_id:
            conditions.append(Download.device_id == device_id)
        stmt = delete(Download).where(and_(*conditions))
        res = await db.execute(stmt)
        await db.commit()
        return res.rowcount


download_service = DownloadService()
