import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.config.settings import settings
from app.models.song import Song


async def seed_song(db: AsyncSession) -> Song:
    song = Song(
        external_id="song-lyrics-1",
        title="Test Track",
        artist_name="Test Artist",
        album_name="Test Album",
        duration=200,
    )
    db.add(song)
    await db.commit()
    await db.refresh(song)
    return song


@pytest.mark.asyncio
async def test_get_lyrics_no_lyrics_state(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    song = await seed_song(db_session)

    res = await client.get(f"/api/songs/{song.id}/lyrics", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["has_lyrics"] is False
    assert data["is_synced"] is False
    assert data["plain_text"] is None


@pytest.mark.asyncio
async def test_get_lyrics_unknown_song(client: AsyncClient, auth_headers: dict, monkeypatch):
    from app.services.catalog_service import catalog_service

    async def no_upstream_match(_ids):
        return []

    monkeypatch.setattr(catalog_service.gaana, "get_track_info", no_upstream_match)

    res = await client.get("/api/songs/does-not-exist/lyrics", headers=auth_headers)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_upsert_lyrics_requires_token(client: AsyncClient, db_session: AsyncSession):
    song = await seed_song(db_session)

    res = await client.put(f"/api/songs/{song.id}/lyrics", json={"plain_text": "La la la"})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "lyrics_write_disabled"


@pytest.mark.asyncio
async def test_upsert_lyrics_rejects_bad_token(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(settings, "LYRICS_ADMIN_TOKEN", "secret-token")
    song = await seed_song(db_session)

    res = await client.put(
        f"/api/songs/{song.id}/lyrics",
        json={"plain_text": "La la la"},
        headers={"X-Lyrics-Admin-Token": "wrong-token"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_upsert_and_get_plain_lyrics(client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(settings, "LYRICS_ADMIN_TOKEN", "secret-token")
    song = await seed_song(db_session)

    put_res = await client.put(
        f"/api/songs/{song.id}/lyrics",
        json={"plain_text": "Line one\nLine two", "language": "English"},
        headers={"X-Lyrics-Admin-Token": "secret-token"},
    )
    assert put_res.status_code == 200
    data = put_res.json()["data"]
    assert data["has_lyrics"] is True
    assert data["is_synced"] is False
    assert data["plain_text"] == "Line one\nLine two"

    get_res = await client.get(f"/api/songs/{song.id}/lyrics", headers=auth_headers)
    assert get_res.status_code == 200
    assert get_res.json()["data"]["plain_text"] == "Line one\nLine two"


@pytest.mark.asyncio
async def test_upsert_synced_lyrics_and_delete(client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(settings, "LYRICS_ADMIN_TOKEN", "secret-token")
    song = await seed_song(db_session)
    headers = {"X-Lyrics-Admin-Token": "secret-token"}

    put_res = await client.put(
        f"/api/songs/{song.id}/lyrics",
        json={"synced_lines": [{"time_ms": 0, "text": "Line one"}, {"time_ms": 3000, "text": "Line two"}]},
        headers=headers,
    )
    assert put_res.status_code == 200
    data = put_res.json()["data"]
    assert data["is_synced"] is True
    assert len(data["synced_lines"]) == 2

    del_res = await client.delete(f"/api/songs/{song.id}/lyrics", headers=headers)
    assert del_res.status_code == 200

    get_res = await client.get(f"/api/songs/{song.id}/lyrics", headers=auth_headers)
    assert get_res.json()["data"]["has_lyrics"] is False


@pytest.mark.asyncio
async def test_upsert_lyrics_rejects_empty_payload(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(settings, "LYRICS_ADMIN_TOKEN", "secret-token")
    song = await seed_song(db_session)

    res = await client.put(
        f"/api/songs/{song.id}/lyrics",
        json={},
        headers={"X-Lyrics-Admin-Token": "secret-token"},
    )
    assert res.status_code == 422
