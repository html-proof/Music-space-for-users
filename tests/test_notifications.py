import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.song import Artist, FollowedArtist, Song
from app.models.user import User
from app.services.notification_service import notification_service


async def provision_user(client: AsyncClient, db: AsyncSession, headers: dict) -> User:
    await client.get("/api/auth/me", headers=headers)
    firebase_uid = headers["Authorization"].split("test_token_")[-1]
    res = await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    return res.scalar_one()


async def seed_artist_and_song(db: AsyncSession, artist_external_id: str = "art-notif-1"):
    artist = Artist(external_id=artist_external_id, name="Test Artist")
    db.add(artist)
    await db.commit()
    await db.refresh(artist)

    song = Song(
        external_id="song-notif-1",
        title="Brand New Track",
        artist_id=artist.id,
        artist_name=artist.name,
        duration=180,
    )
    db.add(song)
    await db.commit()
    await db.refresh(song)
    return artist, song


@pytest.mark.asyncio
async def test_notification_preferences_default_and_update(client: AsyncClient, auth_headers: dict):
    get_res = await client.get("/api/notifications/preferences", headers=auth_headers)
    assert get_res.status_code == 200
    assert get_res.json()["data"]["new_release_songs"] is True

    patch_res = await client.patch(
        "/api/notifications/preferences", json={"new_release_songs": False}, headers=auth_headers
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["data"]["new_release_songs"] is False

    get_res2 = await client.get("/api/notifications/preferences", headers=auth_headers)
    assert get_res2.json()["data"]["new_release_songs"] is False


@pytest.mark.asyncio
async def test_notifications_require_auth(client: AsyncClient):
    res = await client.get("/api/notifications")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_notify_new_song_creates_notifications_for_followers(
    client: AsyncClient, auth_headers: dict, auth_headers_user2: dict, db_session: AsyncSession
):
    user1 = await provision_user(client, db_session, auth_headers)
    user2 = await provision_user(client, db_session, auth_headers_user2)
    artist, song = await seed_artist_and_song(db_session)

    # Only user1 follows the artist.
    db_session.add(FollowedArtist(user_id=user1.id, artist_id=artist.id))
    await db_session.commit()

    notified_count = await notification_service.notify_new_song(db_session, song)
    assert notified_count == 1

    list_res = await client.get("/api/notifications", headers=auth_headers)
    data = list_res.json()["data"]
    assert len(data) == 1
    assert data[0]["category"] == "new_release_song"
    assert data[0]["song_id"] == song.id
    assert data[0]["artist_id"] == artist.id
    assert "Test Artist" in data[0]["title"]
    assert data[0]["is_read"] is False

    other_list_res = await client.get("/api/notifications", headers=auth_headers_user2)
    assert other_list_res.json()["data"] == []


@pytest.mark.asyncio
async def test_notify_new_song_respects_disabled_preference(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    user1 = await provision_user(client, db_session, auth_headers)
    artist, song = await seed_artist_and_song(db_session)
    db_session.add(FollowedArtist(user_id=user1.id, artist_id=artist.id))
    await db_session.commit()

    await client.patch("/api/notifications/preferences", json={"new_release_songs": False}, headers=auth_headers)

    notified_count = await notification_service.notify_new_song(db_session, song)
    assert notified_count == 0

    list_res = await client.get("/api/notifications", headers=auth_headers)
    assert list_res.json()["data"] == []


@pytest.mark.asyncio
async def test_notify_new_song_without_artist_id_is_noop(db_session: AsyncSession):
    song = Song(external_id="song-no-artist", title="Orphan Track", duration=120)
    db_session.add(song)
    await db_session.commit()
    await db_session.refresh(song)

    notified_count = await notification_service.notify_new_song(db_session, song)
    assert notified_count == 0


@pytest.mark.asyncio
async def test_mark_notification_read(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    user1 = await provision_user(client, db_session, auth_headers)
    artist, song = await seed_artist_and_song(db_session)
    db_session.add(FollowedArtist(user_id=user1.id, artist_id=artist.id))
    await db_session.commit()

    await notification_service.notify_new_song(db_session, song)
    list_res = await client.get("/api/notifications", headers=auth_headers)
    notification_id = list_res.json()["data"][0]["id"]

    read_res = await client.patch(f"/api/notifications/{notification_id}/read", headers=auth_headers)
    assert read_res.status_code == 200

    list_res2 = await client.get("/api/notifications", headers=auth_headers)
    assert list_res2.json()["data"][0]["is_read"] is True


@pytest.mark.asyncio
async def test_mark_unknown_notification_read_is_404(client: AsyncClient, auth_headers: dict):
    res = await client.patch("/api/notifications/does-not-exist/read", headers=auth_headers)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_notify_new_song_writes_notification_even_without_device_token(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Firebase emulator mode has no live app to send through -- notify_new_song
    must still record the in-app notification rather than silently doing nothing."""
    user1 = await provision_user(client, db_session, auth_headers)
    artist, song = await seed_artist_and_song(db_session)
    db_session.add(FollowedArtist(user_id=user1.id, artist_id=artist.id))
    await db_session.commit()

    # Confirm no device/push token exists for this user.
    devices = (await db_session.execute(select(Device).where(Device.user_id == user1.id))).scalars().all()
    assert devices == []

    notified_count = await notification_service.notify_new_song(db_session, song)
    assert notified_count == 1
