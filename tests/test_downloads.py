import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Song


async def seed_song(db: AsyncSession, external_id: str = "song-dl-1") -> Song:
    song = Song(
        external_id=external_id,
        title="Test Track",
        artist_name="Test Artist",
        album_name="Test Album",
        duration=210,
        audio_url="https://cdn.example.com/fallback.m3u8",
        stream_urls={
            "very_high_quality": "https://cdn.example.com/vhq.m3u8",
            "high_quality": "https://cdn.example.com/hq.m3u8",
            "medium_quality": "https://cdn.example.com/mq.m3u8",
            "low_quality": "https://cdn.example.com/lq.m3u8",
        },
    )
    db.add(song)
    await db.commit()
    await db.refresh(song)
    return song


@pytest.mark.asyncio
async def test_request_download_unauthorized(client: AsyncClient):
    res = await client.post("/api/downloads", json={"song_id": "x", "device_id": "d1"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_request_download_unknown_song(client: AsyncClient, auth_headers: dict, monkeypatch):
    from app.services.catalog_service import catalog_service

    async def no_upstream_match(_ids):
        return []

    monkeypatch.setattr(catalog_service.gaana, "get_track_info", no_upstream_match)

    res = await client.post(
        "/api/downloads", json={"song_id": "does-not-exist", "device_id": "d1"}, headers=auth_headers
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_request_download_creates_queued_record(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    song = await seed_song(db_session)

    res = await client.post(
        "/api/downloads",
        json={"song_id": song.id, "device_id": "phone-1", "quality": "high_quality"},
        headers=auth_headers,
    )
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["status"] == "queued"
    assert data["progress_percent"] == 0
    assert data["quality"] == "high_quality"
    assert data["audio_url"] == "https://cdn.example.com/hq.m3u8"
    assert data["device_id"] == "phone-1"


@pytest.mark.asyncio
async def test_request_download_defaults_to_high_quality_on_invalid_value(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    song = await seed_song(db_session)

    res = await client.post(
        "/api/downloads",
        json={"song_id": song.id, "device_id": "phone-1", "quality": "ultra-mega"},
        headers=auth_headers,
    )
    assert res.status_code == 201
    assert res.json()["data"]["quality"] == "high_quality"


@pytest.mark.asyncio
async def test_progress_update_lifecycle(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    song = await seed_song(db_session)

    create_res = await client.post(
        "/api/downloads", json={"song_id": song.id, "device_id": "phone-1"}, headers=auth_headers
    )
    download_id = create_res.json()["data"]["id"]

    progress_res = await client.patch(
        f"/api/downloads/{download_id}",
        json={"status": "downloading", "progress_percent": 40},
        headers=auth_headers,
    )
    assert progress_res.status_code == 200
    assert progress_res.json()["data"]["status"] == "downloading"
    assert progress_res.json()["data"]["progress_percent"] == 40

    complete_res = await client.patch(
        f"/api/downloads/{download_id}",
        json={"status": "completed", "file_size_bytes": 5_000_000},
        headers=auth_headers,
    )
    assert complete_res.status_code == 200
    data = complete_res.json()["data"]
    assert data["status"] == "completed"
    assert data["progress_percent"] == 100
    assert data["file_size_bytes"] == 5_000_000
    assert data["completed_at"] is not None


@pytest.mark.asyncio
async def test_failed_download_can_be_retried_by_requesting_again(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    song = await seed_song(db_session)

    create_res = await client.post(
        "/api/downloads", json={"song_id": song.id, "device_id": "phone-1"}, headers=auth_headers
    )
    download_id = create_res.json()["data"]["id"]

    await client.patch(
        f"/api/downloads/{download_id}",
        json={"status": "failed", "error_message": "network error"},
        headers=auth_headers,
    )

    retry_res = await client.post(
        "/api/downloads", json={"song_id": song.id, "device_id": "phone-1"}, headers=auth_headers
    )
    assert retry_res.status_code == 201
    data = retry_res.json()["data"]
    assert data["id"] == download_id
    assert data["status"] == "queued"
    assert data["progress_percent"] == 0
    assert data["error_message"] is None


@pytest.mark.asyncio
async def test_update_unknown_download_is_404(client: AsyncClient, auth_headers: dict):
    res = await client.patch("/api/downloads/does-not-exist", json={"progress_percent": 10}, headers=auth_headers)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_list_downloads_filters_by_device_and_status(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    song1 = await seed_song(db_session, "song-dl-a")
    song2 = await seed_song(db_session, "song-dl-b")

    await client.post("/api/downloads", json={"song_id": song1.id, "device_id": "phone-1"}, headers=auth_headers)
    r2 = await client.post("/api/downloads", json={"song_id": song2.id, "device_id": "tablet-1"}, headers=auth_headers)
    await client.patch(
        f"/api/downloads/{r2.json()['data']['id']}", json={"status": "completed"}, headers=auth_headers
    )

    all_res = await client.get("/api/downloads", headers=auth_headers)
    assert len(all_res.json()["data"]) == 2

    phone_res = await client.get("/api/downloads?device_id=phone-1", headers=auth_headers)
    assert len(phone_res.json()["data"]) == 1
    assert phone_res.json()["data"][0]["device_id"] == "phone-1"

    completed_res = await client.get("/api/downloads?status=completed", headers=auth_headers)
    assert len(completed_res.json()["data"]) == 1
    assert completed_res.json()["data"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_storage_summary(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    song1 = await seed_song(db_session, "song-dl-c")
    song2 = await seed_song(db_session, "song-dl-d")

    r1 = await client.post("/api/downloads", json={"song_id": song1.id, "device_id": "phone-1"}, headers=auth_headers)
    r2 = await client.post("/api/downloads", json={"song_id": song2.id, "device_id": "phone-1"}, headers=auth_headers)
    await client.patch(
        f"/api/downloads/{r1.json()['data']['id']}",
        json={"status": "completed", "file_size_bytes": 3_000_000},
        headers=auth_headers,
    )
    await client.patch(
        f"/api/downloads/{r2.json()['data']['id']}",
        json={"status": "downloading", "progress_percent": 50},
        headers=auth_headers,
    )

    summary_res = await client.get("/api/downloads/storage", headers=auth_headers)
    assert summary_res.status_code == 200
    data = summary_res.json()["data"]
    assert data["total_downloads"] == 2
    assert data["completed_downloads"] == 1
    assert data["total_bytes"] == 3_000_000
    assert data["by_status"]["completed"] == 1
    assert data["by_status"]["downloading"] == 1


@pytest.mark.asyncio
async def test_delete_single_and_all_downloads(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    song1 = await seed_song(db_session, "song-dl-e")
    song2 = await seed_song(db_session, "song-dl-f")

    r1 = await client.post("/api/downloads", json={"song_id": song1.id, "device_id": "phone-1"}, headers=auth_headers)
    await client.post("/api/downloads", json={"song_id": song2.id, "device_id": "phone-1"}, headers=auth_headers)

    del_res = await client.delete(f"/api/downloads/{r1.json()['data']['id']}", headers=auth_headers)
    assert del_res.status_code == 200

    del_missing_res = await client.delete(f"/api/downloads/{r1.json()['data']['id']}", headers=auth_headers)
    assert del_missing_res.status_code == 404

    list_res = await client.get("/api/downloads", headers=auth_headers)
    assert len(list_res.json()["data"]) == 1

    del_all_res = await client.delete("/api/downloads", headers=auth_headers)
    assert del_all_res.status_code == 200
    assert del_all_res.json()["data"]["deleted"] == 1

    list_res_empty = await client.get("/api/downloads", headers=auth_headers)
    assert len(list_res_empty.json()["data"]) == 0
