"""Operations on the durable catalog sync queue.

Everything that changes a job's state lives here so the rules hold in one
place:

* enqueueing is idempotent -- an unfinished job for the same entity is raised
  in priority, never duplicated;
* a job is claimed by moving it to PROCESSING, so two workers (or two
  instances) cannot both run it;
* a job becomes COMPLETED only after the catalog write it describes has
  committed;
* a failure records the reason and schedules a retry with exponential backoff,
  and out-of-attempts means FAILED, which is a resting state, not a deletion;
* only jobs that finished long ago are archived, and only archived rows are
  ever deleted.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models.catalog_sync import (
    ACTIVE_STATUSES,
    ARCHIVED,
    COMPLETED,
    FAILED,
    PENDING,
    PRIORITY_BACKGROUND,
    PROCESSING,
    CatalogSyncJob,
)

logger = logging.getLogger("catalog_sync_service")

# Retry backoff: attempt 1 waits a minute, then 2, 4, 8... capped so a job that
# keeps failing still gets retried at a sane interval rather than in a year.
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def backoff_for(attempts: int) -> timedelta:
    return timedelta(seconds=min(RETRY_BASE_SECONDS * (2 ** max(attempts - 1, 0)), RETRY_MAX_SECONDS))


class CatalogSyncService:
    # -- enqueue ----------------------------------------------------------

    @staticmethod
    async def enqueue(
        db: AsyncSession,
        entity_type: str,
        external_id: str,
        *,
        priority: int = PRIORITY_BACKGROUND,
        entity_id: Optional[str] = None,
        parent_job_id: Optional[str] = None,
        max_attempts: Optional[int] = None,
        commit: bool = True,
    ) -> CatalogSyncJob:
        """Queue `external_id` for sync, or raise the priority of the open job.

        Idempotent by (entity_type, external_id) across unfinished jobs, which
        is what stops the same album from being queued once per screen open. An
        existing job is never reset -- its attempts and history stay -- it only
        gains priority and, if it was waiting out a backoff it no longer needs
        to, an earlier retry time.
        """
        existing = await CatalogSyncService.active_job(db, entity_type, external_id)
        if existing is not None:
            changed = False
            if priority > existing.priority:
                existing.priority = priority
                # A newly urgent job should not sit out the rest of a backoff
                # that was scheduled while it was background work.
                existing.next_retry_at = None
                changed = True
            if entity_id and not existing.entity_id:
                existing.entity_id = entity_id
                changed = True
            if changed and commit:
                await db.commit()
            return existing

        job = CatalogSyncJob(
            entity_type=entity_type,
            external_id=external_id,
            entity_id=entity_id,
            priority=priority,
            parent_job_id=parent_job_id,
            max_attempts=max_attempts or settings.CATALOG_SYNC_MAX_ATTEMPTS,
            status=PENDING,
        )
        db.add(job)
        if commit:
            await db.commit()
        else:
            await db.flush()
        return job

    @staticmethod
    async def enqueue_many(
        db: AsyncSession, specs: Sequence[dict], *, commit: bool = True
    ) -> int:
        """Enqueue a batch, skipping entities that already have an open job.

        One round trip for the existence check instead of one per entity, so a
        page of search results costs a single query rather than twenty.
        """
        specs = [s for s in specs if s.get("external_id")]
        if not specs:
            return 0

        wanted = {(s["entity_type"], s["external_id"]) for s in specs}
        open_jobs = await CatalogSyncService.active_jobs_for(db, wanted)
        by_key = {(j.entity_type, j.external_id): j for j in open_jobs}

        created = 0
        for spec in specs:
            key = (spec["entity_type"], spec["external_id"])
            job = by_key.get(key)
            priority = spec.get("priority", PRIORITY_BACKGROUND)
            if job is not None:
                if priority > job.priority:
                    job.priority = priority
                    job.next_retry_at = None
                if spec.get("entity_id") and not job.entity_id:
                    job.entity_id = spec["entity_id"]
                continue
            db.add(CatalogSyncJob(
                entity_type=spec["entity_type"],
                external_id=spec["external_id"],
                entity_id=spec.get("entity_id"),
                priority=priority,
                parent_job_id=spec.get("parent_job_id"),
                max_attempts=settings.CATALOG_SYNC_MAX_ATTEMPTS,
                status=PENDING,
            ))
            by_key[key] = None  # deduplicate within this batch too
            created += 1

        if commit:
            await db.commit()
        return created

    # -- lookups ----------------------------------------------------------

    @staticmethod
    async def active_job(
        db: AsyncSession, entity_type: str, external_id: str
    ) -> Optional[CatalogSyncJob]:
        stmt = select(CatalogSyncJob).where(
            CatalogSyncJob.entity_type == entity_type,
            CatalogSyncJob.external_id == external_id,
            CatalogSyncJob.status.in_(ACTIVE_STATUSES),
        )
        return (await db.execute(stmt)).scalars().first()

    @staticmethod
    async def active_jobs_for(
        db: AsyncSession, keys: Iterable[tuple]
    ) -> List[CatalogSyncJob]:
        keys = list(keys)
        if not keys:
            return []
        externals = {k[1] for k in keys}
        types = {k[0] for k in keys}
        stmt = select(CatalogSyncJob).where(
            CatalogSyncJob.entity_type.in_(types),
            CatalogSyncJob.external_id.in_(externals),
            CatalogSyncJob.status.in_(ACTIVE_STATUSES),
        )
        rows = list((await db.execute(stmt)).scalars().all())
        wanted = set(keys)
        return [r for r in rows if (r.entity_type, r.external_id) in wanted]

    @staticmethod
    async def counts_by_status(db: AsyncSession) -> dict:
        rows = await db.execute(
            select(CatalogSyncJob.status, func.count()).group_by(CatalogSyncJob.status)
        )
        return {status: count for status, count in rows.all()}

    # -- worker transitions -----------------------------------------------

    @staticmethod
    async def reclaim_stale(db: AsyncSession) -> int:
        """Return jobs abandoned by a dead worker to PENDING.

        A process killed mid-fetch leaves its jobs in PROCESSING forever, which
        is indistinguishable from work in flight -- and the partial unique index
        means that stuck row also blocks any new job for the same entity. The
        attempt is counted: a job that reliably kills its worker must eventually
        reach FAILED rather than cycle forever.
        """
        cutoff = utcnow() - timedelta(seconds=settings.CATALOG_SYNC_STALE_SECONDS)
        stmt = select(CatalogSyncJob).where(
            CatalogSyncJob.status == PROCESSING,
            CatalogSyncJob.updated_at < cutoff,
        )
        stale = list((await db.execute(stmt)).scalars().all())
        for job in stale:
            job.attempts += 1
            if job.attempts >= job.max_attempts:
                job.status = FAILED
                job.error_message = "abandoned in PROCESSING by a worker that did not finish"
            else:
                job.status = PENDING
                job.next_retry_at = utcnow() + backoff_for(job.attempts)
        if stale:
            await db.commit()
        return len(stale)

    @staticmethod
    async def claim(db: AsyncSession, limit: int) -> List[CatalogSyncJob]:
        """Take up to `limit` due jobs, highest priority first.

        Claiming is a status change, not a delete: the row stays in the table
        for the whole of its processing, so a crash leaves evidence to reclaim
        rather than silently losing the work.
        """
        now = utcnow()
        stmt = (
            select(CatalogSyncJob)
            .where(
                CatalogSyncJob.status == PENDING,
                or_(
                    CatalogSyncJob.next_retry_at.is_(None),
                    CatalogSyncJob.next_retry_at <= now,
                ),
            )
            .order_by(CatalogSyncJob.priority.desc(), CatalogSyncJob.created_at.asc())
            .limit(limit)
        )
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            # Two instances running the worker must not claim the same rows.
            # SQLite has neither SKIP LOCKED nor concurrent writers to protect
            # against, so it takes the plain query.
            stmt = stmt.with_for_update(skip_locked=True)

        jobs = list((await db.execute(stmt)).scalars().all())
        for job in jobs:
            job.status = PROCESSING
            job.attempts += 1
            job.error_message = None
        if jobs:
            await db.commit()
        return jobs

    @staticmethod
    async def complete(db: AsyncSession, job: CatalogSyncJob, entity_id: Optional[str] = None) -> None:
        """Mark done. Only ever called after the catalog write has committed."""
        job.status = COMPLETED
        job.completed_at = utcnow()
        job.error_message = None
        job.next_retry_at = None
        if entity_id:
            job.entity_id = entity_id
        await db.commit()

    @staticmethod
    async def fail(db: AsyncSession, job: CatalogSyncJob, error: str) -> None:
        """Record why, and either schedule a retry or rest in FAILED."""
        job.error_message = (error or "")[:2000]
        if job.attempts >= job.max_attempts:
            job.status = FAILED
            job.next_retry_at = None
            logger.warning(
                "catalog sync job %s (%s %s) failed permanently after %d attempts: %s",
                job.id, job.entity_type, job.external_id, job.attempts, job.error_message,
            )
        else:
            job.status = PENDING
            job.next_retry_at = utcnow() + backoff_for(job.attempts)
        await db.commit()

    # -- priority ---------------------------------------------------------

    @staticmethod
    async def prioritize_songs(
        db: AsyncSession, wanted: Sequence[tuple], *, commit: bool = True
    ) -> int:
        """Raise (or create) sync jobs for `[(song_id, priority), ...]`.

        What the user is listening to right now has to be synchronized before
        the background backlog, so playback re-prioritizes the jobs behind the
        current track and the one after it. A track that is already fully
        stored needs no job created -- there is nothing left to fetch -- but an
        open job for it is still raised, so a track whose row is a placeholder
        stops waiting behind a page of search results.
        """
        from app.models.song import Song  # local: avoids an import cycle

        by_id = {str(sid): priority for sid, priority in wanted if sid}
        if not by_id:
            return 0

        rows = (await db.execute(
            select(Song.id, Song.external_id, Song.audio_url, Song.stream_urls)
            .where(Song.id.in_(list(by_id)))
        )).all()

        touched = 0
        for song_id, external_id, audio_url, stream_urls in rows:
            if not external_id:
                continue
            priority = by_id[str(song_id)]
            playable = bool(audio_url) or bool(stream_urls)
            job = await CatalogSyncService.active_job(db, "song", external_id)
            if job is None:
                if playable:
                    # Nothing outstanding to fetch.
                    continue
                await CatalogSyncService.enqueue(
                    db, "song", external_id,
                    priority=priority, entity_id=str(song_id), commit=False,
                )
                touched += 1
            elif priority > job.priority:
                job.priority = priority
                job.next_retry_at = None
                touched += 1

        if touched and commit:
            await db.commit()
        return touched

    # -- retention --------------------------------------------------------

    @staticmethod
    async def archive_and_prune(db: AsyncSession) -> dict:
        """Age COMPLETED jobs into ARCHIVED, then delete old ARCHIVED rows.

        The only path by which a job row leaves the table, and it cannot touch
        PENDING, PROCESSING or FAILED: a job is retained for the whole of its
        working life plus a retention window, and only the archived tail is
        collected.
        """
        now = utcnow()
        # synchronize_session=False throughout: the default tries to replay the
        # WHERE clause in Python against loaded objects, which both re-evaluates
        # timestamps SQLite hands back naive (against aware ones here) and
        # touches rows this statement is deleting. The session is expired below
        # instead, which is correct and cheaper.
        archived = (await db.execute(
            update(CatalogSyncJob)
            .where(
                CatalogSyncJob.status == COMPLETED,
                CatalogSyncJob.completed_at
                < now - timedelta(seconds=settings.CATALOG_SYNC_RETENTION_SECONDS),
            )
            .values(status=ARCHIVED)
            .execution_options(synchronize_session=False)
        )).rowcount or 0

        deleted = (await db.execute(
            delete(CatalogSyncJob)
            .where(
                CatalogSyncJob.status == ARCHIVED,
                CatalogSyncJob.updated_at
                < now - timedelta(seconds=settings.CATALOG_SYNC_ARCHIVE_SECONDS),
            )
            .execution_options(synchronize_session=False)
        )).rowcount or 0

        if archived or deleted:
            await db.commit()
            db.expire_all()
        return {"jobs_archived": archived, "jobs_deleted": deleted}


catalog_sync_service = CatalogSyncService()
