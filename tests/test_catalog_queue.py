"""The catalog write queue and the album cap it enforces.

Requests no longer write the Gaana rows they touch; they resolve an id and
queue the row, and a background pass every CATALOG_FLUSH_INTERVAL_SECONDS
writes the batch and trims `albums` back to CATALOG_MAX_ALBUMS. The properties
that makes safe are pinned here, because each of them fails silently:

* the id a response hands out is the id the row ends up with -- otherwise every
  like, playlist entry and download made in that window points at nothing;
* a write path that turns one of those ids into a foreign key flushes first;
* eviction never touches a row a user still references;
* a sparser copy of a track refreshes what it knows and blanks nothing.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models.song import Album, Artist, LikedSong, Song
from app.services.catalog_queue import catalog_queue
from app.services.catalog_service import catalog_service
from app.workers.catalog_writer_worker import evict_old_albums, run_once

from tests.test_catalog_user_separation import RAW_TRACK, stub_gaana


@pytest.mark.asyncio
async def test_search_queues_the_write_and_the_flush_keeps_the_id(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch,
):
    """The id in the response is the id in the table, one flush later."""
    stub_gaana(monkeypatch)

    res = await client.get("/api/search?query=Shared&type=track", headers=auth_headers)
    assert res.status_code == 200, res.text
    returned_id = res.json()["data"]["songs"][0]["id"]

    # Nothing written yet: the row is still in the queue.
    assert await db_session.scalar(select(func.count()).select_from(Song)) == 0
    assert catalog_queue.is_pending(returned_id)

    await run_once(db_session)

    song = (
        await db_session.execute(select(Song).where(Song.external_id == RAW_TRACK["seokey"]))
    ).scalar_one()
    assert str(song.id) == returned_id
    assert song.title == "Shared Track"
    assert song.stream_urls == {"high_quality": "https://stream/hq.mp4"}
    # The artist and album it hangs off went with it.
    assert (await db_session.execute(select(Artist))).scalars().first().name == "Shared Artist"
    assert (await db_session.execute(select(Album))).scalars().first().title == "Shared Album"


@pytest.mark.asyncio
async def test_liking_a_queued_song_flushes_it_first(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch,
):
    """`liked_songs.song_id` is a real FK, so the row has to exist by then."""
    stub_gaana(monkeypatch)

    res = await client.get("/api/search?query=Shared&type=track", headers=auth_headers)
    song_id = res.json()["data"]["songs"][0]["id"]
    assert catalog_queue.is_pending(song_id)

    res = await client.post(f"/api/songs/{song_id}/like", headers=auth_headers)
    assert res.status_code in (200, 201), res.text

    liked = (await db_session.execute(select(LikedSong))).scalars().one()
    assert str(liked.song_id) == song_id
    assert not catalog_queue.is_pending(song_id)


@pytest.mark.asyncio
async def test_flush_does_not_blank_fields_a_sparser_copy_omits(db_session: AsyncSession):
    """Gaana returns tracks without stream urls; a re-touch must not wipe them."""
    await catalog_service.upsert_gaana_song(db_session, RAW_TRACK)
    await catalog_queue.flush(db_session)

    sparse = {**RAW_TRACK, "stream_urls": {}, "images": {}, "duration": "0"}
    await catalog_service.upsert_gaana_song(db_session, sparse)
    await catalog_queue.flush(db_session)

    song = (
        await db_session.execute(select(Song).where(Song.external_id == RAW_TRACK["seokey"]))
    ).scalar_one()
    assert song.stream_urls == {"high_quality": "https://stream/hq.mp4"}
    assert song.thumbnail_url == "https://img/large.jpg"
    assert song.duration == 200


@pytest.mark.asyncio
async def test_eviction_trims_to_the_cap_newest_first(
    db_session: AsyncSession, monkeypatch,
):
    monkeypatch.setattr(settings, "CATALOG_MAX_ALBUMS", 2)
    # updated_at set explicitly: CURRENT_TIMESTAMP on SQLite only has second
    # resolution, so five albums written in one test would all tie and the
    # "least recently touched" ordering would not actually be exercised.
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(5):
        db_session.add(Album(
            external_id=f"album-{i}",
            title=f"Album {i}",
            updated_at=base + timedelta(days=i),
        ))
    await db_session.commit()

    stats = await evict_old_albums(db_session)

    kept = sorted(
        (await db_session.execute(select(Album.external_id))).scalars().all()
    )
    assert kept == ["album-3", "album-4"]
    assert stats["albums_removed"] == 3


@pytest.mark.asyncio
async def test_eviction_spares_anything_a_user_still_references(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch,
):
    """A liked song keeps its album alive however far past the cap it is."""
    stub_gaana(monkeypatch)

    res = await client.get("/api/search?query=Shared&type=track", headers=auth_headers)
    song_id = res.json()["data"]["songs"][0]["id"]
    await client.post(f"/api/songs/{song_id}/like", headers=auth_headers)

    # Everything is now over the cap: without the reference check, all of it goes.
    monkeypatch.setattr(settings, "CATALOG_MAX_ALBUMS", 0)
    await evict_old_albums(db_session)

    song = (await db_session.execute(select(Song).where(Song.id == song_id))).scalar_one()
    assert (
        await db_session.execute(select(Album).where(Album.id == song.album_id))
    ).scalar_one() is not None
