"""The durable catalog sync queue.

Every property here is one that fails silently if it regresses -- a job
quietly vanishing looks exactly like a job that finished. So each rule the
queue promises is pinned:

* an unfinished job survives everything: a new request, a different album, the
  player moving on, another worker cycle, a process restart;
* the same entity never gets two open jobs;
* a job reaches COMPLETED only after its row is actually stored;
* a failure keeps the job, with the reason and a backoff;
* an album job spawns independent track jobs and finishing does not take them
  with it;
* only long-finished jobs are ever deleted.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models.catalog_sync import (
    ALBUM,
    ARCHIVED,
    COMPLETED,
    FAILED,
    PENDING,
    PRIORITY_ALBUM_TRACK,
    PRIORITY_BACKGROUND,
    PRIORITY_PLAYING,
    PRIORITY_REQUESTED,
    PROCESSING,
    SONG,
    CatalogSyncJob,
)
from app.models.song import Album, Song
from app.services.catalog_queue import catalog_queue
from app.services.catalog_service import catalog_service
from app.services.catalog_sync_service import catalog_sync_service
from app.workers.catalog_sync_worker import process_due_jobs
from app.workers.catalog_writer_worker import evict_old_albums, run_once

from tests.test_catalog_user_separation import RAW_TRACK, stub_gaana

ALBUM_SEOKEY = "shared-album"

RAW_ALBUM = {
    "seokey": ALBUM_SEOKEY,
    "album_id": "8484",
    "title": "Shared Album",
    "artists": "Shared Artist",
    "artist_seokeys": "shared-artist",
    "artist_ids": "4242",
    "language": "Malayalam",
    "track_count": "2",
    "release_date": "2026-01-01",
    "images": {"urls": {"large_artwork": "https://img/album.jpg"}},
    "tracks": [
        {**RAW_TRACK, "seokey": "album-track-1", "title": "Album Track 1"},
        {**RAW_TRACK, "seokey": "album-track-2", "title": "Album Track 2"},
    ],
}


def stub_album_and_tracks(monkeypatch, album=RAW_ALBUM, fail_tracks=False):
    """Gaana serving one album and the tracks on it."""
    tracks = {t["seokey"]: t for t in album["tracks"]}

    async def get_album_info(album_ids, info=False, *args, **kwargs):
        return [album] if album["seokey"] in album_ids else {"error": "Unable to find any results!"}

    async def get_track_info(seokeys, *args, **kwargs):
        if fail_tracks:
            return {"error": "Unable to find any results!"}
        found = [tracks[k] for k in seokeys if k in tracks]
        return found or {"error": "Unable to find any results!"}

    monkeypatch.setattr(catalog_service.gaana, "get_album_info", get_album_info)
    monkeypatch.setattr(catalog_service.gaana, "get_track_info", get_track_info)


async def jobs_in(db: AsyncSession, **filters) -> list:
    stmt = select(CatalogSyncJob)
    for column, value in filters.items():
        stmt = stmt.where(getattr(CatalogSyncJob, column) == value)
    return list((await db.execute(stmt.order_by(CatalogSyncJob.created_at))).scalars().all())


# -- enqueue / idempotency ------------------------------------------------

@pytest.mark.asyncio
async def test_a_second_request_reuses_the_open_job_and_raises_its_priority(
    db_session: AsyncSession,
):
    first = await catalog_sync_service.enqueue(
        db_session, SONG, "track-1", priority=PRIORITY_BACKGROUND
    )
    second = await catalog_sync_service.enqueue(
        db_session, SONG, "track-1", priority=PRIORITY_PLAYING
    )

    assert second.id == first.id
    assert len(await jobs_in(db_session)) == 1
    assert second.priority == PRIORITY_PLAYING


@pytest.mark.asyncio
async def test_enqueue_does_not_reset_an_existing_jobs_history(db_session: AsyncSession):
    """Re-requesting must not wipe the attempts that led to a backoff."""
    job = await catalog_sync_service.enqueue(db_session, SONG, "track-1")
    job.attempts = 3
    job.error_message = "upstream 500"
    await db_session.commit()

    again = await catalog_sync_service.enqueue(
        db_session, SONG, "track-1", priority=PRIORITY_REQUESTED
    )
    assert again.attempts == 3
    assert again.error_message == "upstream 500"


@pytest.mark.asyncio
async def test_a_completed_job_does_not_block_a_later_resync(db_session: AsyncSession):
    first = await catalog_sync_service.enqueue(db_session, SONG, "track-1")
    await catalog_sync_service.complete(db_session, first, entity_id=None)

    second = await catalog_sync_service.enqueue(db_session, SONG, "track-1")
    assert second.id != first.id
    assert second.status == PENDING


# -- persistence ----------------------------------------------------------

@pytest.mark.asyncio
async def test_unfinished_jobs_survive_other_requests_and_worker_cycles(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch,
):
    """The core rule: nothing deletes a job just because something else happened."""
    stub_gaana(monkeypatch)
    stub_album_and_tracks(monkeypatch, fail_tracks=True)

    job_a = await catalog_sync_service.enqueue(db_session, ALBUM, "album-a")
    job_b = await catalog_sync_service.enqueue(db_session, SONG, "song-b")
    # Pretend a worker picked B up and is still on it.
    job_b.status = PROCESSING
    await db_session.commit()

    # All the things that must not disturb the queue.
    await client.get("/api/search?query=Shared&type=all", headers=auth_headers)
    await client.get("/api/home/feed", headers=auth_headers)
    await run_once(db_session)      # a full worker cycle
    await run_once(db_session)      # and the next one

    surviving = {j.external_id for j in await jobs_in(db_session)}
    assert "album-a" in surviving
    assert "song-b" in surviving

    refreshed_b = (await db_session.execute(
        select(CatalogSyncJob).where(CatalogSyncJob.external_id == "song-b")
    )).scalar_one()
    # Still PROCESSING: a cycle running while another worker holds a job must
    # not reclaim it before it goes stale.
    assert refreshed_b.status == PROCESSING


@pytest.mark.asyncio
async def test_a_search_records_durable_jobs_for_the_ids_it_hands_out(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch,
):
    """The id in the response outlives the process that minted it."""
    stub_gaana(monkeypatch)

    res = await client.get("/api/search?query=Shared&type=track", headers=auth_headers)
    song_id = res.json()["data"]["songs"][0]["id"]

    job = (await db_session.execute(
        select(CatalogSyncJob).where(CatalogSyncJob.external_id == RAW_TRACK["seokey"])
    )).scalar_one()
    assert job.entity_type == SONG
    assert str(job.entity_id) == song_id
    assert job.status == PENDING


@pytest.mark.asyncio
async def test_the_worker_stores_a_restarted_jobs_entity_under_the_promised_id(
    db_session: AsyncSession, monkeypatch,
):
    """A crash between handing out an id and writing the row is recoverable."""
    stub_album_and_tracks(monkeypatch)
    promised_id = "11111111-2222-3333-4444-555555555555"
    await catalog_sync_service.enqueue(
        db_session, SONG, "album-track-1", entity_id=promised_id
    )
    # Everything the dead process knew is gone.
    catalog_queue.__dict__.update(type(catalog_queue)().__dict__)

    await process_due_jobs(db_session, limit=10)

    song = (await db_session.execute(
        select(Song).where(Song.external_id == "album-track-1")
    )).scalar_one()
    assert str(song.id) == promised_id


# -- processing -----------------------------------------------------------

@pytest.mark.asyncio
async def test_an_album_job_stores_the_album_and_queues_independent_track_jobs(
    db_session: AsyncSession, monkeypatch,
):
    stub_album_and_tracks(monkeypatch)
    await catalog_sync_service.enqueue(db_session, ALBUM, ALBUM_SEOKEY, priority=PRIORITY_REQUESTED)

    # One pass: the album job only. Its track jobs are queued, not run.
    await process_due_jobs(db_session, limit=1)

    album_job = (await db_session.execute(
        select(CatalogSyncJob).where(CatalogSyncJob.entity_type == ALBUM)
    )).scalar_one()
    assert album_job.status == COMPLETED
    assert album_job.completed_at is not None

    album = (await db_session.execute(
        select(Album).where(Album.external_id == RAW_ALBUM["album_id"])
    )).scalar_one()
    assert album.title == "Shared Album"
    assert album.release_date == "2026-01-01"

    track_jobs = await jobs_in(db_session, entity_type=SONG)
    assert {j.external_id for j in track_jobs} == {"album-track-1", "album-track-2"}
    # The album finishing does not finish, or remove, its tracks.
    assert all(j.status == PENDING for j in track_jobs)
    assert all(j.priority == PRIORITY_ALBUM_TRACK for j in track_jobs)
    assert all(str(j.parent_job_id) == str(album_job.id) for j in track_jobs)


@pytest.mark.asyncio
async def test_track_jobs_outlive_their_album_job_being_archived(
    db_session: AsyncSession, monkeypatch,
):
    """Archiving the parent must not cascade into unfinished children."""
    stub_album_and_tracks(monkeypatch)
    await catalog_sync_service.enqueue(db_session, ALBUM, ALBUM_SEOKEY)
    await process_due_jobs(db_session, limit=1)

    album_job = (await db_session.execute(
        select(CatalogSyncJob).where(CatalogSyncJob.entity_type == ALBUM)
    )).scalar_one()
    album_job.completed_at = datetime.now(timezone.utc) - timedelta(days=365)
    album_job.updated_at = datetime.now(timezone.utc) - timedelta(days=365)
    await db_session.commit()

    # Archiving stamps updated_at, so the archived row only becomes eligible
    # for deletion a retention window after that -- two passes, as in life.
    await catalog_sync_service.archive_and_prune(db_session)
    await db_session.refresh(album_job)
    assert album_job.status == ARCHIVED
    album_job.updated_at = datetime.now(timezone.utc) - timedelta(days=365)
    await db_session.commit()
    await catalog_sync_service.archive_and_prune(db_session)

    assert not await jobs_in(db_session, entity_type=ALBUM)
    # The unfinished track jobs are still there, parentless but intact.
    track_jobs = await jobs_in(db_session, entity_type=SONG)
    assert len(track_jobs) == 2
    assert all(j.status == PENDING for j in track_jobs)


@pytest.mark.asyncio
async def test_a_job_is_only_completed_once_its_row_is_stored(
    db_session: AsyncSession, monkeypatch,
):
    stub_album_and_tracks(monkeypatch)
    await catalog_sync_service.enqueue(db_session, SONG, "album-track-1")

    await process_due_jobs(db_session, limit=10)

    job = (await db_session.execute(
        select(CatalogSyncJob).where(CatalogSyncJob.external_id == "album-track-1")
    )).scalar_one()
    assert job.status == COMPLETED
    song = (await db_session.execute(
        select(Song).where(Song.external_id == "album-track-1")
    )).scalar_one()
    assert str(job.entity_id) == str(song.id)


# -- failure handling -----------------------------------------------------

@pytest.mark.asyncio
async def test_an_upstream_failure_keeps_the_job_and_schedules_a_retry(
    db_session: AsyncSession, monkeypatch,
):
    stub_album_and_tracks(monkeypatch, fail_tracks=True)
    await catalog_sync_service.enqueue(db_session, SONG, "album-track-1")

    stats = await process_due_jobs(db_session, limit=10)
    assert stats["jobs_failed"] == 1

    job = (await db_session.execute(select(CatalogSyncJob))).scalar_one()
    assert job.status == PENDING          # kept, not deleted
    assert job.attempts == 1
    assert job.error_message
    assert job.next_retry_at is not None  # and backed off


@pytest.mark.asyncio
async def test_a_job_out_of_attempts_rests_in_failed_rather_than_disappearing(
    db_session: AsyncSession, monkeypatch,
):
    stub_album_and_tracks(monkeypatch, fail_tracks=True)
    job = await catalog_sync_service.enqueue(db_session, SONG, "album-track-1")
    job.max_attempts = 1
    await db_session.commit()

    await process_due_jobs(db_session, limit=10)

    job = (await db_session.execute(select(CatalogSyncJob))).scalar_one()
    assert job.status == FAILED
    assert job.error_message
    # And a later cycle leaves it alone rather than retrying or dropping it.
    await process_due_jobs(db_session, limit=10)
    assert len(await jobs_in(db_session)) == 1


@pytest.mark.asyncio
async def test_a_job_stuck_in_processing_is_reclaimed_only_once_stale(
    db_session: AsyncSession, monkeypatch,
):
    monkeypatch.setattr(settings, "CATALOG_SYNC_STALE_SECONDS", 600)
    job = await catalog_sync_service.enqueue(db_session, SONG, "abandoned")
    job.status = PROCESSING
    await db_session.commit()

    assert await catalog_sync_service.reclaim_stale(db_session) == 0

    job.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.commit()

    assert await catalog_sync_service.reclaim_stale(db_session) == 1
    await db_session.refresh(job)
    assert job.status == PENDING
    assert job.attempts == 1


# -- priority -------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_playing_song_is_processed_before_the_background_backlog(
    db_session: AsyncSession, monkeypatch,
):
    stub_album_and_tracks(monkeypatch)
    for i in range(5):
        await catalog_sync_service.enqueue(
            db_session, SONG, f"backlog-{i}", priority=PRIORITY_BACKGROUND
        )
    await catalog_sync_service.enqueue(
        db_session, SONG, "album-track-1", priority=PRIORITY_PLAYING
    )

    claimed = await catalog_sync_service.claim(db_session, limit=1)
    assert [j.external_id for j in claimed] == ["album-track-1"]


@pytest.mark.asyncio
async def test_playing_a_song_raises_its_open_sync_job(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch,
):
    stub_gaana(monkeypatch)
    res = await client.get("/api/search?query=Shared&type=track", headers=auth_headers)
    song_id = res.json()["data"]["songs"][0]["id"]

    job = (await db_session.execute(
        select(CatalogSyncJob).where(CatalogSyncJob.entity_type == SONG)
    )).scalar_one()
    assert job.priority == PRIORITY_BACKGROUND

    res = await client.post(
        "/api/player/play", json={"song_id": song_id}, headers=auth_headers
    )
    assert res.status_code == 200, res.text

    await db_session.refresh(job)
    assert job.priority == PRIORITY_PLAYING


# -- retention ------------------------------------------------------------

@pytest.mark.asyncio
async def test_only_long_finished_jobs_are_archived_and_collected(db_session: AsyncSession):
    old = datetime.now(timezone.utc) - timedelta(days=365)

    done = await catalog_sync_service.enqueue(db_session, SONG, "done")
    await catalog_sync_service.complete(db_session, done)
    fresh_done = await catalog_sync_service.enqueue(db_session, SONG, "fresh")
    await catalog_sync_service.complete(db_session, fresh_done)
    pending = await catalog_sync_service.enqueue(db_session, SONG, "pending")
    failed = await catalog_sync_service.enqueue(db_session, SONG, "failed")
    failed.status = FAILED
    done.completed_at = old
    await db_session.commit()

    await catalog_sync_service.archive_and_prune(db_session)

    await db_session.refresh(done)
    assert done.status == ARCHIVED
    await db_session.refresh(fresh_done)
    assert fresh_done.status == COMPLETED   # inside the retention window
    await db_session.refresh(pending)
    assert pending.status == PENDING        # never touched
    await db_session.refresh(failed)
    assert failed.status == FAILED          # never touched

    # Only once the archived row is old enough does it actually go.
    done.updated_at = old
    await db_session.commit()
    stats = await catalog_sync_service.archive_and_prune(db_session)
    assert stats["jobs_deleted"] == 1
    assert not await jobs_in(db_session, external_id="done")


# -- interaction with catalog eviction ------------------------------------

@pytest.mark.asyncio
async def test_eviction_spares_an_album_an_unfinished_job_still_points_at(
    db_session: AsyncSession, monkeypatch,
):
    album = Album(external_id="pending-album", title="Pending Album")
    db_session.add(album)
    await db_session.commit()
    await db_session.refresh(album)

    await catalog_sync_service.enqueue(
        db_session, ALBUM, "pending-album", entity_id=album.id
    )

    monkeypatch.setattr(settings, "CATALOG_MAX_ALBUMS", 0)
    await evict_old_albums(db_session)

    assert await db_session.get(Album, album.id) is not None
