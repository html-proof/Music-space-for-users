"""The catalog synchronization pass, run every CATALOG_FLUSH_INTERVAL_SECONDS.

One cycle, in this order:

1. Drain `catalog_queue` -- the songs/albums/artists that requests resolved ids
   for but deliberately did not write inline.
2. Work the durable `catalog_sync_jobs` queue: reclaim jobs abandoned by a dead
   worker, claim the highest-priority due ones, fetch and store each entity,
   and mark them COMPLETED only once the write has committed. Jobs that fail
   are retried with backoff and end at FAILED, never deleted.
3. Age long-completed jobs into ARCHIVED and collect the old archived tail.
4. Evict albums beyond CATALOG_MAX_ALBUMS, least-recently-touched first.

The cycle synchronizes; it never deletes and re-fetches. Existing catalog rows
are upserted in place, and nothing here removes a job because a newer request
arrived, the player moved on, or the timer came round again.

The eviction exists because `albums` and `songs` are a cache of Gaana keyed by
whatever anyone has ever searched for, so left alone they grow without bound
and never shrink -- a mirror of Gaana rather than a working set. Trimming to a
fixed cap by `updated_at` keeps what is actually being played and drops the
long tail of one-off search results.

Nothing a user still points at is ever evicted, whatever its age: an album is
protected if it is saved, or if any of its songs is liked, in a playlist, in
listening history, downloaded, or attached to a notification. Those rows are
user data, and `liked_songs` / `playlist_songs` / `downloads` are FKs that
would cascade the delete straight into it. The protected set is computed as
subqueries rather than fetched into Python, so the cost does not scale with the
size of the library.
"""
import asyncio
import logging

from sqlalchemy import delete, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.database import async_session_factory
from app.models.download import Download
from app.models.history import ListeningHistory
from app.models.notification import Notification
from app.models.playlist import PlaylistSong
from app.models.catalog_sync import ACTIVE_STATUSES, ALBUM, SONG, CatalogSyncJob
from app.models.song import Album, LikedSong, SavedAlbum, Song
from app.services.catalog_queue import catalog_queue
from app.services.catalog_sync_service import catalog_sync_service
from app.workers.catalog_sync_worker import process_due_jobs

logger = logging.getLogger("catalog_writer_worker")

# Let the app finish starting before the first pass competes for the pool.
INITIAL_DELAY_SECONDS = 30
ERROR_BACKOFF_SECONDS = 60


def _protected_song_ids():
    """Subquery: every song id some user row references."""
    return union(
        select(LikedSong.song_id),
        select(PlaylistSong.song_id),
        select(ListeningHistory.song_id),
        select(Download.song_id),
        select(Notification.song_id).where(Notification.song_id.is_not(None)),
    )


def _job_referenced_entity_ids(entity_type):
    """Subquery: entity ids an unfinished sync job is still working on.

    Evicting one of these would delete the row a PENDING or PROCESSING job is
    about to write into -- and, worse, the row whose id a client is already
    holding while it waits for that job.
    """
    return (
        select(CatalogSyncJob.entity_id)
        .where(
            CatalogSyncJob.entity_type == entity_type,
            CatalogSyncJob.entity_id.is_not(None),
            CatalogSyncJob.status.in_(ACTIVE_STATUSES),
        )
    )


def _doomed_albums():
    """Subquery: albums outside the cap that nothing still protects."""
    keep = (
        select(Album.id)
        .order_by(Album.updated_at.desc())
        .limit(settings.CATALOG_MAX_ALBUMS)
        .subquery()
    )
    protected_by_song = (
        select(Song.album_id)
        .where(Song.album_id.is_not(None), Song.id.in_(_protected_song_ids()))
    )
    return (
        select(Album.id)
        .where(
            Album.id.not_in(select(keep.c.id)),
            Album.id.not_in(select(SavedAlbum.album_id)),
            Album.id.not_in(protected_by_song),
            Album.id.not_in(_job_referenced_entity_ids(ALBUM)),
        )
    )


async def evict_old_albums(db: AsyncSession) -> dict:
    """Trim `albums` to CATALOG_MAX_ALBUMS, oldest-touched first."""
    doomed = _doomed_albums().subquery()
    album_ids = select(doomed.c.id)

    # Songs of an evicted album go with it, so the cache actually shrinks --
    # `songs.album_id` is ON DELETE SET NULL, so deleting only the album would
    # leave every track behind as an orphan row. A protected song stays, and
    # simply loses its album reference.
    songs_removed = (
        await db.execute(
            delete(Song).where(
                Song.album_id.in_(album_ids),
                Song.id.not_in(_protected_song_ids()),
                Song.id.not_in(_job_referenced_entity_ids(SONG)),
            )
        )
    ).rowcount or 0
    albums_removed = (
        await db.execute(delete(Album).where(Album.id.in_(album_ids)))
    ).rowcount or 0
    await db.commit()
    return {"albums_removed": albums_removed, "songs_removed": songs_removed}


async def run_once(db: AsyncSession) -> dict:
    """One full synchronization cycle against the given session."""
    written = await catalog_queue.flush(db)
    stats = {f"{kind}s_written": count for kind, count in written.items()}

    if settings.CATALOG_SYNC_ENABLED:
        stats.update(await process_due_jobs(db, settings.CATALOG_SYNC_BATCH_SIZE))
        stats.update(await catalog_sync_service.archive_and_prune(db))

    stats.update(await evict_old_albums(db))
    return stats


async def catalog_writer_loop():
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    interval = max(int(settings.CATALOG_FLUSH_INTERVAL_SECONDS), 10)
    while True:
        try:
            async with async_session_factory() as db:
                stats = await run_once(db)
            if any(stats.values()):
                logger.info("catalog flush: %s", stats)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            # Last chance to persist what is queued before the process goes
            # away; ids already handed to clients point at these rows.
            try:
                async with async_session_factory() as db:
                    await catalog_queue.flush(db)
            except Exception:
                logger.warning("final catalog flush failed; queued rows dropped", exc_info=True)
            raise
        except Exception:
            logger.exception("catalog flush pass failed")
            await asyncio.sleep(ERROR_BACKOFF_SECONDS)
