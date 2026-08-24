"""Processing half of the durable catalog sync queue.

Fetches one album or song per job from Gaana, validates the response, upserts
it, and only then marks the job COMPLETED. Everything about *when* jobs run
lives in the worker loop; everything about *what a status means* lives in
catalog_sync_service.

An album job stores the album and then queues one song job per track, at album
priority. Those song jobs are independent from that moment on: the album job
completing says nothing about them, and they stay in the queue at whatever
state they have reached until each one finishes on its own.
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog_sync import (
    ALBUM,
    PRIORITY_ALBUM_TRACK,
    SONG,
    CatalogSyncJob,
)
from app.services.catalog_queue import catalog_queue
from app.services.catalog_service import catalog_service
from app.services.catalog_sync_service import catalog_sync_service

logger = logging.getLogger("catalog_sync_worker")


class SyncDataError(Exception):
    """The upstream response was missing or unusable. Retryable."""


def _first_valid(raw: Any) -> Optional[Dict[str, Any]]:
    """The one usable record in a Gaana response, or None.

    Gaana signals "no such thing" as a dict with an `error` key and a partial
    outage as an empty list, neither of which raises. Validating before the
    upsert is what keeps a job from being marked COMPLETED over a record that
    was never stored.
    """
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return None
    first = raw[0]
    if not isinstance(first, dict) or "error" in first or not first.get("seokey"):
        return None
    return first


async def process_song_job(db: AsyncSession, job: CatalogSyncJob) -> str:
    """Fetch and store one track. Returns the local song id."""
    if job.entity_id:
        # Honour the id a client is already holding for this track (see
        # catalog_queue.adopt_id): after a restart the upsert would otherwise
        # mint a new one and strand it.
        catalog_queue.adopt_id("song", job.external_id, job.entity_id)

    raw = await catalog_service.gaana.get_track_info([job.external_id])
    track = _first_valid(raw)
    if track is None:
        raise SyncDataError(f"no usable track data for {job.external_id!r}")

    song = await catalog_service.upsert_gaana_song(db, track)
    # The write must be on disk before the job may be called complete.
    await catalog_queue.ensure_persisted(db, song.id)
    await catalog_service.register_sync_jobs(db)
    return song.id


async def process_album_job(db: AsyncSession, job: CatalogSyncJob) -> str:
    """Fetch and store one album, then queue a job per track.

    The track jobs are created in the same transaction as the album write, so
    an album can never end up stored with its tracks silently unqueued.
    """
    if job.entity_id:
        catalog_queue.adopt_id("album", job.external_id, job.entity_id)

    raw = await catalog_service.gaana.get_album_info([job.external_id], True)
    album_data = _first_valid(raw)
    if album_data is None:
        raise SyncDataError(f"no usable album data for {job.external_id!r}")

    album = await catalog_service._upsert_gaana_album(db, album_data)
    await db.commit()

    tracks = album_data.get("tracks")
    specs: List[dict] = []
    if isinstance(tracks, list):
        for track in tracks:
            if isinstance(track, dict) and track.get("seokey"):
                specs.append({
                    "entity_type": SONG,
                    "external_id": track["seokey"],
                    "priority": PRIORITY_ALBUM_TRACK,
                    "parent_job_id": job.id,
                })
    if specs:
        created = await catalog_sync_service.enqueue_many(db, specs)
        logger.debug("album %s queued %d track jobs", job.external_id, created)
    return album.id


async def process_job(db: AsyncSession, job: CatalogSyncJob) -> bool:
    """Run one claimed job to a terminal-for-now state. Never raises.

    A failure is recorded on the job -- attempts, message, next retry -- rather
    than propagated, so one bad album cannot stop the rest of the batch.
    """
    try:
        if job.entity_type == ALBUM:
            entity_id = await process_album_job(db, job)
        elif job.entity_type == SONG:
            entity_id = await process_song_job(db, job)
        else:
            raise SyncDataError(f"unknown entity_type {job.entity_type!r}")
    except Exception as exc:
        await db.rollback()
        logger.warning(
            "catalog sync job %s (%s %s) attempt %d failed: %s",
            job.id, job.entity_type, job.external_id, job.attempts, exc,
        )
        await catalog_sync_service.fail(db, job, f"{type(exc).__name__}: {exc}")
        return False

    await catalog_sync_service.complete(db, job, entity_id)
    return True


async def process_due_jobs(db: AsyncSession, limit: int) -> dict:
    """Reclaim what was abandoned, then work through the highest-priority due jobs."""
    reclaimed = await catalog_sync_service.reclaim_stale(db)
    jobs = await catalog_sync_service.claim(db, limit)

    completed = failed = 0
    for job in jobs:
        if await process_job(db, job):
            completed += 1
        else:
            failed += 1

    return {
        "jobs_reclaimed": reclaimed,
        "jobs_completed": completed,
        "jobs_failed": failed,
    }
