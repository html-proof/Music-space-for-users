import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.song import Artist, FollowedArtist, Song
from app.models.user import User
from app.services.catalog_service import catalog_service
from app.workers.release_watch_worker import check_new_releases_once


async def provision_user(client: AsyncClient, db: AsyncSession, headers: dict) -> User:
    await client.get("/api/auth/me", headers=headers)
    firebase_uid = headers["Authorization"].split("test_token_")[-1]
    res = await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    return res.scalar_one()


def _raw_track(seokey: str, title: str) -> dict:
    return {
        "seokey": seokey,
        "track_id": seokey,
        "title": title,
        "artists": "Test Artist",
        "album": "Single",
        "duration": "200",
        "images": {"urls": {"large_artwork": "https://cdn.example.com/art.jpg"}},
        "stream_urls": {"urls": {"high_quality": "https://cdn.example.com/stream.m3u8"}},
        "language": "English",
        "genres": "Pop",
        "is_explicit": False,
    }


@pytest.mark.asyncio
async def test_check_new_releases_discovers_and_notifies(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    user1 = await provision_user(client, db_session, auth_headers)
    artist = Artist(external_id="gaana-artist-1", name="Test Artist")
    db_session.add(artist)
    await db_session.commit()
    await db_session.refresh(artist)

    db_session.add(FollowedArtist(user_id=user1.id, artist_id=artist.id))
    await db_session.commit()

    async def fake_get_top_tracks(artist_id, limit=10, page=1):
        assert artist_id == "gaana-artist-1"
        return {"tracks": [_raw_track("new-track-1", "Fresh Drop")], "total": 1}

    monkeypatch.setattr(catalog_service.gaana, "get_top_tracks", fake_get_top_tracks)

    summary = await check_new_releases_once(db_session)
    assert summary["artists_followed"] == 1
    assert summary["artists_checked"] == 1
    assert summary["new_songs"] == 1
    assert summary["notifications_sent"] == 1

    song = (await db_session.execute(select(Song).where(Song.external_id == "new-track-1"))).scalar_one()
    assert song.title == "Fresh Drop"

    list_res = await client.get("/api/notifications", headers=auth_headers)
    data = list_res.json()["data"]
    assert len(data) == 1
    assert data[0]["song_id"] == song.id


@pytest.mark.asyncio
async def test_check_new_releases_skips_already_known_songs(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    user1 = await provision_user(client, db_session, auth_headers)
    artist = Artist(external_id="gaana-artist-2", name="Known Artist")
    db_session.add(artist)
    await db_session.commit()
    await db_session.refresh(artist)

    existing_song = Song(
        external_id="already-known",
        title="Old Song",
        artist_id=artist.id,
        artist_name=artist.name,
        duration=180,
    )
    db_session.add(existing_song)
    db_session.add(FollowedArtist(user_id=user1.id, artist_id=artist.id))
    await db_session.commit()

    async def fake_get_top_tracks(artist_id, limit=10, page=1):
        return {"tracks": [_raw_track("already-known", "Old Song")], "total": 1}

    monkeypatch.setattr(catalog_service.gaana, "get_top_tracks", fake_get_top_tracks)

    summary = await check_new_releases_once(db_session)
    assert summary["new_songs"] == 0
    assert summary["notifications_sent"] == 0

    list_res = await client.get("/api/notifications", headers=auth_headers)
    assert list_res.json()["data"] == []


@pytest.mark.asyncio
async def test_check_new_releases_with_no_followed_artists(db_session: AsyncSession):
    summary = await check_new_releases_once(db_session)
    assert summary["artists_followed"] == 0
    assert summary["new_songs"] == 0


@pytest.mark.asyncio
async def test_check_new_releases_tolerates_upstream_failure(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    user1 = await provision_user(client, db_session, auth_headers)
    artist = Artist(external_id="gaana-artist-3", name="Flaky Artist")
    db_session.add(artist)
    await db_session.commit()
    await db_session.refresh(artist)
    db_session.add(FollowedArtist(user_id=user1.id, artist_id=artist.id))
    await db_session.commit()

    async def failing_get_top_tracks(artist_id, limit=10, page=1):
        raise RuntimeError("upstream unreachable")

    monkeypatch.setattr(catalog_service.gaana, "get_top_tracks", failing_get_top_tracks)

    summary = await check_new_releases_once(db_session)
    assert summary["artists_checked"] == 1
    assert summary["new_songs"] == 0
