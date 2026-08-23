import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Song


async def seed_test_song(db: AsyncSession) -> Song:
    song = Song(
        external_id="test-playback-song",
        title="Midnight City",
        artist_name="M83",
        album_name="Hurry Up, We're Dreaming",
        duration=243,
        audio_url="https://cdn.example.com/hls/song.m3u8",
        stream_urls={"very_high_quality": "https://cdn.example.com/hls/320.m3u8"},
        language="English",
        genre="Electronic",
        mood="Euphoric"
    )
    db.add(song)
    await db.commit()
    await db.refresh(song)
    return song


@pytest.mark.asyncio
async def test_playback_flow(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    song = await seed_test_song(db_session)

    # 1. Get initial state
    init_res = await client.get("/api/player/current", headers=auth_headers)
    assert init_res.status_code == 200
    assert init_res.json()["data"]["state"] == "stopped"

    # 2. Play
    play_payload = {
        "song_id": song.id,
        "device_id": "phone-1",
        "position_seconds": 0.0,
        "queue": [song.id]
    }
    play_res = await client.post("/api/player/play", json=play_payload, headers=auth_headers)
    assert play_res.status_code == 200
    data = play_res.json()["data"]
    assert data["state"] == "playing"
    assert data["song_id"] == song.id
    assert data["song"]["title"] == "Midnight City"

    # 3. Pause
    pause_res = await client.post("/api/player/pause", json={"device_id": "phone-1", "position_seconds": 45.0}, headers=auth_headers)
    assert pause_res.status_code == 200
    assert pause_res.json()["data"]["state"] == "paused"
    assert pause_res.json()["data"]["position_seconds"] == 45.0

    # 4. Resume
    resume_res = await client.post("/api/player/resume", json={"device_id": "phone-1"}, headers=auth_headers)
    assert resume_res.status_code == 200
    assert resume_res.json()["data"]["state"] == "playing"

    # 5. Seek
    seek_res = await client.post("/api/player/seek", json={"device_id": "phone-1", "position_seconds": 120.0}, headers=auth_headers)
    assert seek_res.status_code == 200
    assert seek_res.json()["data"]["position_seconds"] == 120.0

    # 6. Stop
    stop_res = await client.post("/api/player/stop", headers=auth_headers)
    assert stop_res.status_code == 200
    assert stop_res.json()["data"]["state"] == "stopped"


@pytest.mark.asyncio
async def test_playback_telemetry_event(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    song = await seed_test_song(db_session)
    event_payload = {
        "device_id": "laptop-1",
        "song_id": song.id,
        "event": "play",
        "position": 0.0,
        "duration": 243.0,
        "metadata": {"network": "wifi"}
    }
    res = await client.post("/api/player/events", json=event_payload, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "recorded"
