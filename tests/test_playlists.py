import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Song


async def seed_playlist_songs(db: AsyncSession):
    s1 = Song(external_id="pl-s1", title="Song One", artist_name="Artist A", duration=180)
    s2 = Song(external_id="pl-s2", title="Song Two", artist_name="Artist B", duration=210)
    s3 = Song(external_id="pl-s3", title="Song Three", artist_name="Artist C", duration=240)
    db.add(s1)
    db.add(s2)
    db.add(s3)
    await db.commit()
    await db.refresh(s1)
    await db.refresh(s2)
    await db.refresh(s3)
    return s1, s2, s3


@pytest.mark.asyncio
async def test_playlist_crud_and_reorder(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    s1, s2, s3 = await seed_playlist_songs(db_session)

    # 1. Create Playlist
    pl_payload = {
        "title": "My Weekend Mix",
        "description": "Chill vibes for the weekend",
        "is_public": True
    }
    create_res = await client.post("/api/playlists", json=pl_payload, headers=auth_headers)
    assert create_res.status_code == 201
    playlist_id = create_res.json()["data"]["id"]

    # 2. Add songs in order
    await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s1.id}, headers=auth_headers)
    await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s2.id}, headers=auth_headers)
    await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s3.id}, headers=auth_headers)

    # 3. Retrieve playlist and verify initial order [s1, s2, s3]
    get_res = await client.get(f"/api/playlists/{playlist_id}", headers=auth_headers)
    assert get_res.status_code == 200
    songs = get_res.json()["data"]["songs"]
    assert len(songs) == 3
    assert songs[0]["song_id"] == s1.id
    assert songs[1]["song_id"] == s2.id
    assert songs[2]["song_id"] == s3.id

    # 4. Reorder songs to [s3, s1, s2]
    reorder_payload = {"song_ids": [s3.id, s1.id, s2.id]}
    reorder_res = await client.patch(f"/api/playlists/{playlist_id}/reorder", json=reorder_payload, headers=auth_headers)
    assert reorder_res.status_code == 200

    # Verify new sequence
    updated_res = await client.get(f"/api/playlists/{playlist_id}", headers=auth_headers)
    updated_songs = updated_res.json()["data"]["songs"]
    assert updated_songs[0]["song_id"] == s3.id
    assert updated_songs[1]["song_id"] == s1.id
    assert updated_songs[2]["song_id"] == s2.id

    # 5. Remove song
    del_song_res = await client.delete(f"/api/playlists/{playlist_id}/songs/{s2.id}", headers=auth_headers)
    assert del_song_res.status_code == 200

    # 6. Delete Playlist
    del_pl_res = await client.delete(f"/api/playlists/{playlist_id}", headers=auth_headers)
    assert del_pl_res.status_code == 200
