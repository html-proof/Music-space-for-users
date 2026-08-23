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


@pytest.mark.asyncio
async def test_private_collaborative_playlist_is_visible_and_editable_by_non_owner(
    client: AsyncClient, auth_headers: dict, auth_headers_user2: dict, db_session: AsyncSession
):
    """A private (is_public=False) but collaborative playlist must still be
    reachable by a non-owner -- otherwise is_collaborative is meaningless,
    since add_song/remove_song/reorder_songs all authorize through the same
    get_playlist() visibility check."""
    s1, _, _ = await seed_playlist_songs(db_session)

    create_res = await client.post(
        "/api/playlists",
        json={"title": "Shared list", "is_public": False, "is_collaborative": True},
        headers=auth_headers,
    )
    playlist_id = create_res.json()["data"]["id"]

    get_res = await client.get(f"/api/playlists/{playlist_id}", headers=auth_headers_user2)
    assert get_res.status_code == 200

    add_res = await client.post(
        f"/api/playlists/{playlist_id}/songs", json={"song_id": s1.id}, headers=auth_headers_user2
    )
    assert add_res.status_code == 201


@pytest.mark.asyncio
async def test_private_noncollaborative_playlist_still_blocks_non_owner(
    client: AsyncClient, auth_headers: dict, auth_headers_user2: dict, db_session: AsyncSession
):
    s1, _, _ = await seed_playlist_songs(db_session)

    create_res = await client.post(
        "/api/playlists",
        json={"title": "Just mine", "is_public": False, "is_collaborative": False},
        headers=auth_headers,
    )
    playlist_id = create_res.json()["data"]["id"]

    get_res = await client.get(f"/api/playlists/{playlist_id}", headers=auth_headers_user2)
    assert get_res.status_code == 404

    add_res = await client.post(
        f"/api/playlists/{playlist_id}/songs", json={"song_id": s1.id}, headers=auth_headers_user2
    )
    assert add_res.status_code == 403


@pytest.mark.asyncio
async def test_add_song_with_explicit_position_shifts_existing_entries(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """An explicit `position` on a brand-new entry must displace whatever was
    already there, not silently collide with it (PlaylistSong has no
    uniqueness constraint on (playlist_id, position) to catch a collision)."""
    s1, s2, s3 = await seed_playlist_songs(db_session)

    create_res = await client.post("/api/playlists", json={"title": "Ordered"}, headers=auth_headers)
    playlist_id = create_res.json()["data"]["id"]

    await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s1.id}, headers=auth_headers)
    await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s2.id}, headers=auth_headers)
    # Insert s3 at position 0 -- s1 and s2 must shift to 1 and 2.
    insert_res = await client.post(
        f"/api/playlists/{playlist_id}/songs", json={"song_id": s3.id, "position": 0}, headers=auth_headers
    )
    assert insert_res.status_code == 201

    get_res = await client.get(f"/api/playlists/{playlist_id}", headers=auth_headers)
    songs = get_res.json()["data"]["songs"]
    positions = {s["song_id"]: s["position"] for s in songs}
    assert len(set(positions.values())) == 3, f"duplicate positions: {positions}"
    assert positions[s3.id] == 0
    assert positions[s1.id] == 1
    assert positions[s2.id] == 2
