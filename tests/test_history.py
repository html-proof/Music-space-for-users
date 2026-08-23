import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Song
from app.services.history_service import HistoryService
from app.services.auth_service import AuthService


async def seed_song(db: AsyncSession) -> Song:
    song = Song(
        external_id="history-test-song",
        title="Blinding Lights",
        artist_name="The Weeknd",
        album_name="After Hours",
        duration=200,
        audio_url="https://cdn.example.com/song.m3u8",
        language="English",
        genre="Synthwave"
    )
    db.add(song)
    await db.commit()
    await db.refresh(song)
    return song


@pytest.mark.asyncio
async def test_listening_history_thresholds(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    # Ensure user exists
    me_res = await client.get("/api/auth/me", headers=auth_headers)
    user_id = me_res.json()["data"]["id"]
    song = await seed_song(db_session)

    # 1. Casual skip (< 30s and < 50%)
    await HistoryService.record_history(
        db=db_session,
        user_id=user_id,
        song_id=song.id,
        duration_listened=10.0,
        completion_percentage=5.0,
        skipped=False
    )

    # 2. Meaningful listen (duration >= 30s)
    await HistoryService.record_history(
        db=db_session,
        user_id=user_id,
        song_id=song.id,
        duration_listened=45.0,
        completion_percentage=22.5,
        skipped=False
    )

    # Fetch history via API
    res = await client.get("/api/history", headers=auth_headers)
    assert res.status_code == 200
    items = res.json()["data"]["items"]
    assert len(items) == 2
    # Check that meaningful listen has skipped=False and short listen has skipped=True
    assert items[0]["skipped"] is False
    assert items[1]["skipped"] is True


@pytest.mark.asyncio
async def test_search_history(client: AsyncClient, auth_headers: dict):
    # Log searches
    await client.get("/api/search?query=Daft+Punk", headers=auth_headers)
    await client.get("/api/search?query=Weekend", headers=auth_headers)

    # Retrieve search history
    res = await client.get("/api/search/history", headers=auth_headers)
    assert res.status_code == 200
    history = res.json()["data"]
    assert len(history) == 2
    queries = [h["query"] for h in history]
    assert "Daft Punk" in queries
    assert "Weekend" in queries

    # Clear search history
    del_res = await client.delete("/api/search/history", headers=auth_headers)
    assert del_res.status_code == 200

    empty_res = await client.get("/api/search/history", headers=auth_headers)
    assert len(empty_res.json()["data"]) == 0
