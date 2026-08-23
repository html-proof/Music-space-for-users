import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Song, Album, Artist


async def seed_library_entities(db: AsyncSession):
    artist = Artist(external_id="art-lib-1", name="Daft Punk")
    album = Album(external_id="alb-lib-1", title="Discovery", artist_name="Daft Punk")
    song = Song(
        external_id="song-lib-1",
        title="One More Time",
        artist_name="Daft Punk",
        album_name="Discovery",
        duration=320,
        audio_url="https://cdn.example.com/one-more-time.m3u8"
    )
    db.add(artist)
    db.add(album)
    db.add(song)
    await db.commit()
    await db.refresh(artist)
    await db.refresh(album)
    await db.refresh(song)
    return song, album, artist


@pytest.mark.asyncio
async def test_liked_songs_lifecycle(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    song, _, _ = await seed_library_entities(db_session)

    # 1. Like song
    like_res = await client.post(f"/api/songs/{song.id}/like", headers=auth_headers)
    assert like_res.status_code == 200
    assert like_res.json()["data"]["is_liked"] is True

    # 2. Get liked collection
    lib_res = await client.get("/api/library/liked", headers=auth_headers)
    assert lib_res.status_code == 200
    liked = lib_res.json()["data"]
    assert len(liked) == 1
    assert liked[0]["id"] == song.id

    # 3. Unlike song
    unlike_res = await client.delete(f"/api/songs/{song.id}/like", headers=auth_headers)
    assert unlike_res.status_code == 200

    # 4. Check empty
    lib_res_empty = await client.get("/api/library/liked", headers=auth_headers)
    assert len(lib_res_empty.json()["data"]) == 0


@pytest.mark.asyncio
async def test_saved_albums_and_artists(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    _, album, artist = await seed_library_entities(db_session)

    # Save Album
    save_res = await client.post(f"/api/albums/{album.id}/save", headers=auth_headers)
    assert save_res.status_code == 200

    albums_res = await client.get("/api/library/albums", headers=auth_headers)
    assert len(albums_res.json()["data"]) == 1

    # Follow Artist
    follow_res = await client.post(f"/api/artists/{artist.id}/follow", headers=auth_headers)
    assert follow_res.status_code == 200

    artists_res = await client.get("/api/library/artists", headers=auth_headers)
    assert len(artists_res.json()["data"]) == 1
