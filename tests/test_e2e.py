import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Song, Album, Artist


async def seed_e2e_music_database(db: AsyncSession):
    artist1 = Artist(external_id="e2e-art-1", name="Jack Harlow", genres=["Hip Hop"])
    artist2 = Artist(external_id="e2e-art-2", name="The Weeknd", genres=["Pop", "R&B"])

    album1 = Album(external_id="e2e-alb-1", title="Tyler Herro", artist_name="Jack Harlow")
    album2 = Album(external_id="e2e-alb-2", title="After Hours", artist_name="The Weeknd")

    song1 = Song(
        external_id="e2e-song-1",
        title="Tyler Herro",
        artist_name="Jack Harlow",
        album_name="Tyler Herro",
        duration=156,
        genre="Hip Hop",
        mood="Energetic",
        audio_url="https://cdn.example.com/tyler-herro.m3u8",
        stream_urls={"very_high_quality": "https://cdn.example.com/tyler-herro-320.m3u8"}
    )
    song2 = Song(
        external_id="e2e-song-2",
        title="Blinding Lights",
        artist_name="The Weeknd",
        album_name="After Hours",
        duration=200,
        genre="Pop",
        mood="Euphoric",
        audio_url="https://cdn.example.com/blinding-lights.m3u8",
        stream_urls={"very_high_quality": "https://cdn.example.com/blinding-lights-320.m3u8"}
    )
    song3 = Song(
        external_id="e2e-song-3",
        title="Save Your Tears",
        artist_name="The Weeknd",
        album_name="After Hours",
        duration=215,
        genre="Pop",
        mood="Chill",
        audio_url="https://cdn.example.com/save-your-tears.m3u8"
    )

    db.add_all([artist1, artist2, album1, album2, song1, song2, song3])
    await db.commit()
    await db.refresh(artist1)
    await db.refresh(artist2)
    await db.refresh(album1)
    await db.refresh(album2)
    await db.refresh(song1)
    await db.refresh(song2)
    await db.refresh(song3)
    return (song1, song2, song3), (album1, album2), (artist1, artist2)


@pytest.mark.asyncio
async def test_complete_e2e_spotify_lifecycle(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    songs, albums, artists = await seed_e2e_music_database(db_session)
    s1, s2, s3 = songs
    alb1, alb2 = albums
    art1, art2 = artists

    # -------------------------------------------------------------
    # 1. AUTHENTICATION & PROFILE SYNC
    # -------------------------------------------------------------
    me_res = await client.get("/api/auth/me", headers=auth_headers)
    assert me_res.status_code == 200
    user = me_res.json()["data"]
    assert user["firebase_uid"] == "user123"

    sync_payload = {
        "display_name": "Sebastian E2E",
        "country": "US",
        "language": "English"
    }
    sync_res = await client.post("/api/auth/sync", json=sync_payload, headers=auth_headers)
    assert sync_res.status_code == 200
    assert sync_res.json()["data"]["display_name"] == "Sebastian E2E"

    # -------------------------------------------------------------
    # 2. USER PREFERENCES
    # -------------------------------------------------------------
    pref_update = {
        "preferred_languages": ["English", "Spanish"],
        "favorite_genres": ["Hip Hop", "Pop"],
        "audio_quality": "very_high",
        "crossfade": 5,
        "explicit_content": True
    }
    pref_res = await client.patch("/api/users/preferences", json=pref_update, headers=auth_headers)
    assert pref_res.status_code == 200
    assert pref_res.json()["data"]["crossfade"] == 5

    # -------------------------------------------------------------
    # 3. DEVICE REGISTRATION & SESSIONS
    # -------------------------------------------------------------
    # Register iPhone
    dev_iphone = {
        "device_id": "iphone-e2e",
        "device_name": "Sebastian's iPhone 15",
        "device_type": "mobile",
        "platform": "iOS",
        "os_version": "17.5"
    }
    reg1_res = await client.post("/api/devices/register", json=dev_iphone, headers=auth_headers)
    assert reg1_res.status_code == 201
    assert "session_token" in reg1_res.json()["data"]

    # Register Mac Desktop
    dev_mac = {
        "device_id": "mac-e2e",
        "device_name": "MacBook Pro M3",
        "device_type": "desktop",
        "platform": "macOS",
        "os_version": "14.4"
    }
    reg2_res = await client.post("/api/devices/register", json=dev_mac, headers=auth_headers)
    assert reg2_res.status_code == 201

    # Heartbeat
    hb_res = await client.post(
        "/api/devices/iphone-e2e/heartbeat",
        json={"is_online": True},
        headers=auth_headers
    )
    assert hb_res.status_code == 200
    assert hb_res.json()["data"]["is_online"] is True

    # -------------------------------------------------------------
    # 4. SEARCH & SEARCH HISTORY
    # -------------------------------------------------------------
    search_res = await client.get("/api/search?query=Harlow&type=track", headers=auth_headers)
    assert search_res.status_code == 200
    assert len(search_res.json()["data"]["songs"]) > 0

    search_hist_res = await client.get("/api/search/history", headers=auth_headers)
    assert search_hist_res.status_code == 200
    assert len(search_hist_res.json()["data"]) >= 1

    # -------------------------------------------------------------
    # 5. PLAYBACK CONTROLS & TELEMETRY EVENTS
    # -------------------------------------------------------------
    # Play track 1
    play_req = {
        "song_id": s1.id,
        "device_id": "iphone-e2e",
        "position_seconds": 0.0,
        "queue": [s2.id, s3.id]
    }
    play_res = await client.post("/api/player/play", json=play_req, headers=auth_headers)
    assert play_res.status_code == 200
    assert play_res.json()["data"]["state"] == "playing"
    assert play_res.json()["data"]["song_id"] == s1.id

    # Seek
    seek_res = await client.post("/api/player/seek", json={"device_id": "iphone-e2e", "position_seconds": 60.0}, headers=auth_headers)
    assert seek_res.status_code == 200
    assert seek_res.json()["data"]["position_seconds"] == 60.0

    # Pause
    pause_res = await client.post("/api/player/pause", json={"device_id": "iphone-e2e"}, headers=auth_headers)
    assert pause_res.status_code == 200
    assert pause_res.json()["data"]["state"] == "paused"

    # Next track from queue
    next_res = await client.post("/api/player/next?device_id=iphone-e2e", headers=auth_headers)
    assert next_res.status_code == 200
    assert next_res.json()["data"]["song_id"] == s2.id

    # Sync player state to Mac
    sync_req = {
        "device_id": "mac-e2e",
        "song_id": s2.id,
        "position_seconds": 30.0,
        "duration_seconds": 200.0,
        "state": "playing",
        "volume": 85,
        "shuffle": True,
        "repeat_mode": "all",
        "queue": [s3.id]
    }
    sync_res = await client.post("/api/player/sync", json=sync_req, headers=auth_headers)
    assert sync_res.status_code == 200
    assert sync_res.json()["data"]["device_id"] == "mac-e2e"
    assert sync_res.json()["data"]["volume"] == 85

    # Telemetry event
    event_payload = {
        "device_id": "mac-e2e",
        "song_id": s2.id,
        "event": "complete",
        "position": 200.0,
        "duration": 200.0
    }
    event_res = await client.post("/api/player/events", json=event_payload, headers=auth_headers)
    assert event_res.status_code == 200

    # -------------------------------------------------------------
    # 6. LIBRARY: LIKES, SAVED ALBUMS, FOLLOWED ARTISTS
    # -------------------------------------------------------------
    # Like song 1 and song 2
    await client.post(f"/api/songs/{s1.id}/like", headers=auth_headers)
    await client.post(f"/api/songs/{s2.id}/like", headers=auth_headers)

    liked_res = await client.get("/api/library/liked", headers=auth_headers)
    assert liked_res.status_code == 200
    assert len(liked_res.json()["data"]) == 2

    # Save album
    await client.post(f"/api/albums/{alb1.id}/save", headers=auth_headers)
    albums_res = await client.get("/api/library/albums", headers=auth_headers)
    assert len(albums_res.json()["data"]) == 1

    # Follow artist
    await client.post(f"/api/artists/{art1.id}/follow", headers=auth_headers)
    artists_res = await client.get("/api/library/artists", headers=auth_headers)
    assert len(artists_res.json()["data"]) == 1

    # -------------------------------------------------------------
    # 7. PLAYLISTS & TRANSACTIONAL REORDERING
    # -------------------------------------------------------------
    pl_create = {
        "title": "E2E Roadtrip Hits",
        "description": "The ultimate highway soundtrack",
        "is_public": True
    }
    pl_res = await client.post("/api/playlists", json=pl_create, headers=auth_headers)
    assert pl_res.status_code == 201
    playlist_id = pl_res.json()["data"]["id"]

    # Add songs in order [s1, s2, s3]
    await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s1.id}, headers=auth_headers)
    await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s2.id}, headers=auth_headers)
    await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s3.id}, headers=auth_headers)

    # Reorder to [s3, s1, s2]
    reorder_res = await client.patch(
        f"/api/playlists/{playlist_id}/reorder",
        json={"song_ids": [s3.id, s1.id, s2.id]},
        headers=auth_headers
    )
    assert reorder_res.status_code == 200

    pl_detail = await client.get(f"/api/playlists/{playlist_id}", headers=auth_headers)
    playlist_songs = pl_detail.json()["data"]["songs"]
    assert playlist_songs[0]["song_id"] == s3.id
    assert playlist_songs[1]["song_id"] == s1.id
    assert playlist_songs[2]["song_id"] == s2.id

    # -------------------------------------------------------------
    # 8. RECOMMENDATIONS & HOME MIXES
    # -------------------------------------------------------------
    rec_res = await client.get("/api/recommendations/home", headers=auth_headers)
    assert rec_res.status_code == 200
    rec_data = rec_res.json()["data"]
    assert "greeting" in rec_data
    assert len(rec_data["categories"]) > 0

    # Similar songs
    sim_res = await client.get(f"/api/recommendations/similar-song/{s1.id}")
    assert sim_res.status_code == 200

    # Mood mix
    mood_res = await client.get("/api/recommendations/mood/Energetic")
    assert mood_res.status_code == 200

    # -------------------------------------------------------------
    # 9. USER BEHAVIOR ANALYTICS PROFILE
    # -------------------------------------------------------------
    analytics_res = await client.get("/api/users/analytics", headers=auth_headers)
    assert analytics_res.status_code == 200
    analytics = analytics_res.json()["data"]
    assert "top_artists" in analytics
    assert "top_genres" in analytics

    # -------------------------------------------------------------
    # 10. CLEANUP & DEVICE REMOVAL
    # -------------------------------------------------------------
    # Remove Mac device
    del_dev = await client.delete("/api/devices/mac-e2e", headers=auth_headers)
    assert del_dev.status_code == 200

    dev_list = await client.get("/api/devices", headers=auth_headers)
    assert len(dev_list.json()["data"]) == 1
    assert dev_list.json()["data"][0]["device_id"] == "iphone-e2e"

    # Delete Account
    del_acc = await client.delete("/api/auth/account", headers=auth_headers)
    assert del_acc.status_code == 200
