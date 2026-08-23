"""
Regression tests for the security and correctness defects found during the
full-system verification pass. Each test names the behaviour that was broken so
a reintroduction fails loudly rather than silently.
"""
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings, settings
from app.config.firebase import verify_firebase_token
from app.middleware.rate_limit import reset_rate_limits
from app.models.playlist import PlaylistSong
from app.models.song import Song
from app.services.playback_service import PlaybackService, PREVIOUS_RESTART_THRESHOLD_SECONDS
from app.services.playlist_service import PlaylistService


async def seed_songs(db: AsyncSession, n: int = 3):
    songs = [
        Song(
            external_id=f"reg-s{i}",
            title=f"Regression Song {i}",
            artist_name=f"Artist {i}",
            duration=200 + i,
        )
        for i in range(n)
    ]
    for s in songs:
        db.add(s)
    await db.commit()
    for s in songs:
        await db.refresh(s)
    return songs


# --------------------------------------------------------------------------
# Authentication bypass
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "token",
    [
        "totally-forged-garbage",
        "aaa.bbb.ccc",
        "victim_uid_12345",
        "Bearer",
        "eyJhbGciOiJub25lIn0.eyJ1aWQiOiJhZG1pbiJ9.",
    ],
)
def test_arbitrary_token_is_never_trusted_as_a_uid(token):
    """
    The verifier used to fall back to `{"uid": token}` for any unrecognised
    string, so knowing a uid was enough to impersonate that account.
    """
    with pytest.raises(ValueError):
        verify_firebase_token(token)


def test_empty_token_rejected():
    with pytest.raises(ValueError):
        verify_firebase_token("")
    with pytest.raises(ValueError):
        verify_firebase_token("   ")


def test_mock_token_without_uid_rejected():
    with pytest.raises(ValueError):
        verify_firebase_token("test_token_")


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["totally-forged-garbage", "victim_uid_12345", "aaa.bbb.ccc"])
async def test_forged_bearer_token_gets_401(client: AsyncClient, token: str):
    res = await client.get("/api/player/current", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    body = res.json()
    assert body["error"]["code"] in ("INVALID_TOKEN", "UNAUTHORIZED")
    # The verifier's internal reason must not leak to the caller.
    assert "Firebase" not in body["error"]["message"]


@pytest.mark.asyncio
async def test_missing_authorization_header_gets_401(client: AsyncClient):
    res = await client.get("/api/player/current")
    assert res.status_code == 401


def test_production_env_refuses_mock_auth():
    """A production deployment with the emulator on has no authentication."""
    with pytest.raises(ValueError):
        Settings(APP_ENV="production", FIREBASE_EMULATOR_ENABLED=True)
    with pytest.raises(ValueError):
        Settings(APP_ENV="Staging", FIREBASE_EMULATOR_ENABLED=True)


def test_production_env_allows_real_auth():
    s = Settings(APP_ENV="production", FIREBASE_EMULATOR_ENABLED=False)
    assert s.is_production() is True


def test_mock_tokens_ignored_in_production(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    try:
        with pytest.raises(ValueError):
            verify_firebase_token("test_token_user123")
    finally:
        monkeypatch.setattr(settings, "APP_ENV", "development")


def test_wildcard_cors_detected():
    assert Settings(CORS_ORIGINS="*").cors_allows_wildcard() is True
    assert Settings(CORS_ORIGINS="https://a.example.com").cors_allows_wildcard() is False


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_returns_429_with_retry_after(client: AsyncClient, auth_headers: dict, monkeypatch):
    """The limiter dependency was imported but never attached to the app."""
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 5)
    reset_rate_limits()

    statuses = []
    for _ in range(8):
        res = await client.get("/api/player/current", headers=auth_headers)
        statuses.append(res.status_code)

    assert 429 in statuses, f"expected a 429 after 5 requests, got {statuses}"
    assert statuses[:5] == [200] * 5

    limited = await client.get("/api/player/current", headers=auth_headers)
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
    assert int(limited.headers["Retry-After"]) >= 1
    assert limited.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_rate_limit_is_per_credential(client: AsyncClient, auth_headers: dict, auth_headers_user2: dict, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 3)
    reset_rate_limits()

    for _ in range(4):
        await client.get("/api/player/current", headers=auth_headers)
    assert (await client.get("/api/player/current", headers=auth_headers)).status_code == 429
    # A different credential must have its own budget.
    assert (await client.get("/api/player/current", headers=auth_headers_user2)).status_code == 200


@pytest.mark.asyncio
async def test_health_check_is_exempt_from_rate_limit(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 2)
    reset_rate_limits()
    for _ in range(10):
        assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_root_and_health_accept_head_requests(client: AsyncClient):
    """Render's port-detection/health probes send HEAD, not GET -- a
    GET-only route answers with 405 and fills the deploy log with noise on
    every probe."""
    assert (await client.head("/")).status_code == 200
    assert (await client.head("/health")).status_code == 200


# --------------------------------------------------------------------------
# Analytics profile shape
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analytics_works_for_brand_new_user(client: AsyncClient, auth_headers: dict):
    """
    The zero-history branch returned bare strings where the schema declares
    List[dict], so every fresh account got a 500 from this endpoint.
    """
    res = await client.get("/api/users/analytics", headers=auth_headers)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    for field in ("top_languages", "top_genres", "top_artists", "top_moods"):
        assert isinstance(data[field], list)
        for item in data[field]:
            assert isinstance(item, dict)
            assert "name" in item and "count" in item


@pytest.mark.asyncio
async def test_analytics_normalises_legacy_string_preferences(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """
    Behaviour profiles were written with bare strings before the shape was
    settled, and those rows are still in the database.
    """
    from app.models.recommendation import UserBehaviorProfile
    from app.models.user import User

    # Materialise the user, then rewrite its profile in the old shape.
    assert (await client.get("/api/users/analytics", headers=auth_headers)).status_code == 200
    user = (await db_session.execute(
        select(User).where(User.firebase_uid == "user123")
    )).scalar_one()

    profile = (await db_session.execute(
        select(UserBehaviorProfile).where(UserBehaviorProfile.user_id == user.id)
    )).scalar_one_or_none()
    if profile is None:
        profile = UserBehaviorProfile(user_id=user.id)
        db_session.add(profile)
    profile.top_languages = ["Hindi", "English"]          # legacy: bare strings
    profile.top_genres = [{"name": "Pop", "count": 7}]    # current shape
    profile.top_artists = [["Arijit Singh", 3]]           # legacy: pair tuples
    profile.top_moods = None                              # never populated
    await db_session.commit()

    res = await client.get("/api/users/analytics", headers=auth_headers)
    assert res.status_code == 200, res.text
    data = res.json()["data"]

    assert {i["name"] for i in data["top_languages"]} == {"Hindi", "English"}
    assert all(i["count"] == 0 for i in data["top_languages"])
    assert data["top_genres"] == [{"name": "Pop", "count": 7}]
    assert data["top_artists"] == [{"name": "Arijit Singh", "count": 3}]
    assert data["top_moods"] == []


# --------------------------------------------------------------------------
# Playlist position integrity
# --------------------------------------------------------------------------

async def positions(db: AsyncSession, playlist_id: str):
    res = await db.execute(
        select(PlaylistSong.song_id, PlaylistSong.position)
        .where(PlaylistSong.playlist_id == playlist_id)
        .order_by(PlaylistSong.position)
    )
    return list(res.all())


async def make_playlist(client: AsyncClient, headers: dict, title: str = "Regression List") -> str:
    res = await client.post("/api/playlists", json={"title": title}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["data"]["id"]


@pytest.mark.asyncio
async def test_sequential_adds_get_unique_contiguous_positions(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    songs = await seed_songs(db_session, 5)
    playlist_id = await make_playlist(client, auth_headers)
    for s in songs:
        res = await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s.id}, headers=auth_headers)
        assert res.status_code == 201, res.text

    rows = await positions(db_session, playlist_id)
    assert [p for _, p in rows] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_adding_the_same_song_twice_is_idempotent(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """A duplicate add used to create a second row sharing a position."""
    songs = await seed_songs(db_session, 1)
    playlist_id = await make_playlist(client, auth_headers)

    first = await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": songs[0].id}, headers=auth_headers)
    second = await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": songs[0].id}, headers=auth_headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["data"]["entry_id"] == second.json()["data"]["entry_id"]

    rows = await positions(db_session, playlist_id)
    assert len(rows) == 1
    assert rows[0][1] == 0


@pytest.mark.asyncio
async def test_partial_reorder_keeps_positions_unique_and_contiguous(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """
    Reordering with a subset used to assign every listed song position 0 and
    leave the omitted ones untouched, producing duplicate positions.
    """
    songs = await seed_songs(db_session, 4)
    playlist_id = await make_playlist(client, auth_headers)
    for s in songs:
        await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s.id}, headers=auth_headers)

    res = await client.patch(
        f"/api/playlists/{playlist_id}/reorder",
        json={"song_ids": [songs[3].id, songs[1].id]},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text

    rows = await positions(db_session, playlist_id)
    assert [p for _, p in rows] == [0, 1, 2, 3]
    assert len({p for _, p in rows}) == 4
    # The listed ids lead, the omitted ones follow in their prior relative order.
    assert [sid for sid, _ in rows] == [songs[3].id, songs[1].id, songs[0].id, songs[2].id]


@pytest.mark.asyncio
async def test_reorder_with_duplicate_ids_does_not_duplicate_positions(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    songs = await seed_songs(db_session, 3)
    playlist_id = await make_playlist(client, auth_headers)
    for s in songs:
        await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s.id}, headers=auth_headers)

    res = await client.patch(
        f"/api/playlists/{playlist_id}/reorder",
        json={"song_ids": [songs[2].id, songs[2].id, songs[0].id]},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text
    rows = await positions(db_session, playlist_id)
    assert [p for _, p in rows] == [0, 1, 2]
    assert [sid for sid, _ in rows] == [songs[2].id, songs[0].id, songs[1].id]


@pytest.mark.asyncio
async def test_reorder_with_foreign_song_id_is_a_client_error(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    songs = await seed_songs(db_session, 2)
    playlist_id = await make_playlist(client, auth_headers)
    await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": songs[0].id}, headers=auth_headers)

    res = await client.patch(
        f"/api/playlists/{playlist_id}/reorder",
        json={"song_ids": [songs[1].id]},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "SONG_NOT_IN_PLAYLIST"


@pytest.mark.asyncio
async def test_removing_a_song_compacts_positions(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    songs = await seed_songs(db_session, 4)
    playlist_id = await make_playlist(client, auth_headers)
    for s in songs:
        await client.post(f"/api/playlists/{playlist_id}/songs", json={"song_id": s.id}, headers=auth_headers)

    res = await client.delete(f"/api/playlists/{playlist_id}/songs/{songs[1].id}", headers=auth_headers)
    assert res.status_code == 200

    rows = await positions(db_session, playlist_id)
    assert [p for _, p in rows] == [0, 1, 2]
    assert [sid for sid, _ in rows] == [songs[0].id, songs[2].id, songs[3].id]


@pytest.mark.asyncio
async def test_add_song_to_other_users_playlist_is_forbidden(
    client: AsyncClient, auth_headers: dict, auth_headers_user2: dict, db_session: AsyncSession
):
    songs = await seed_songs(db_session, 1)
    playlist_id = await make_playlist(client, auth_headers, title="Alice Private")
    res = await client.post(
        f"/api/playlists/{playlist_id}/songs",
        json={"song_id": songs[0].id},
        headers=auth_headers_user2,
    )
    assert res.status_code in (403, 404)


# --------------------------------------------------------------------------
# Playback telemetry validation
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["not_a_real_event", "", "DROP TABLE", "12345"])
async def test_unknown_playback_event_is_rejected(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, event: str
):
    songs = await seed_songs(db_session, 1)
    res = await client.post(
        "/api/player/events",
        json={"device_id": "d1", "song_id": songs[0].id, "event": event},
        headers=auth_headers,
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["play", "PLAY", "Skip", " complete "])
async def test_known_playback_events_accepted_case_insensitively(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, event: str
):
    songs = await seed_songs(db_session, 1)
    res = await client.post(
        "/api/player/events",
        json={"device_id": "d1", "song_id": songs[0].id, "event": event},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_event_accepts_event_type_alias(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    songs = await seed_songs(db_session, 1)
    res = await client.post(
        "/api/player/events",
        json={
            "device_id": "d1",
            "song_id": songs[0].id,
            "event_type": "PAUSE",
            "position_seconds": 12.5,
            "duration_seconds": 200.0,
        },
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_event_for_unknown_song_is_404_not_500(client: AsyncClient, auth_headers: dict, monkeypatch):
    """An unknown song_id used to reach the DB and surface as a 500 FK error."""
    from app.services.catalog_service import catalog_service

    # get_song_by_id falls back to an upstream Gaana lookup on a local miss;
    # stub it so this exercises the router's not-found branch deterministically.
    async def no_upstream_match(_ids):
        return []

    monkeypatch.setattr(catalog_service.gaana, "get_track_info", no_upstream_match)

    res = await client.post(
        "/api/player/events",
        json={"device_id": "d1", "song_id": "does-not-exist", "event": "PLAY"},
        headers=auth_headers,
    )
    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == "SONG_NOT_FOUND"


@pytest.mark.asyncio
async def test_add_unknown_song_to_playlist_is_404(client: AsyncClient, auth_headers: dict, monkeypatch):
    from app.services.catalog_service import catalog_service

    async def no_upstream_match(_ids):
        return []

    monkeypatch.setattr(catalog_service.gaana, "get_track_info", no_upstream_match)

    playlist_id = await make_playlist(client, auth_headers, title="Unknown Song List")
    res = await client.post(
        f"/api/playlists/{playlist_id}/songs",
        json={"song_id": "does-not-exist"},
        headers=auth_headers,
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "SONG_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_mode", ["sometimes", "ALL", "1", ""])
async def test_invalid_repeat_mode_rejected(client: AsyncClient, auth_headers: dict, repeat_mode: str):
    res = await client.post(
        "/api/player/sync",
        json={"device_id": "d1", "repeat_mode": repeat_mode},
        headers=auth_headers,
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_invalid_playback_state_rejected(client: AsyncClient, auth_headers: dict):
    res = await client.post(
        "/api/player/sync",
        json={"device_id": "d1", "state": "vibing"},
        headers=auth_headers,
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_out_of_range_volume_and_position_rejected(client: AsyncClient, auth_headers: dict):
    assert (await client.post(
        "/api/player/sync", json={"device_id": "d1", "volume": 500}, headers=auth_headers
    )).status_code == 422
    assert (await client.post(
        "/api/player/sync", json={"device_id": "d1", "position_seconds": -5}, headers=auth_headers
    )).status_code == 422


# --------------------------------------------------------------------------
# Skip classification and queue transport
# --------------------------------------------------------------------------

def test_near_complete_play_is_not_a_skip():
    """`next` used to mark every advance as a skip, poisoning recommendations."""
    assert PlaybackService._was_skipped(position_seconds=238.0, duration_seconds=240.0) is False
    assert PlaybackService._was_skipped(position_seconds=120.0, duration_seconds=240.0) is False
    # 30s of a very long track: below the completion ratio but a real listen.
    assert PlaybackService._was_skipped(position_seconds=45.0, duration_seconds=3600.0) is False


def test_early_abandon_is_a_skip():
    assert PlaybackService._was_skipped(position_seconds=4.0, duration_seconds=240.0) is True
    assert PlaybackService._was_skipped(position_seconds=0.0, duration_seconds=240.0) is True


def test_zero_duration_does_not_divide_by_zero():
    assert PlaybackService._was_skipped(position_seconds=0.0, duration_seconds=0.0) is True


@pytest.mark.asyncio
async def test_next_advances_and_persists_the_shrinking_queue(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """The queue was mutated in place, so the JSON column never went dirty."""
    songs = await seed_songs(db_session, 3)
    await client.post(
        "/api/player/play",
        json={"song_id": songs[0].id, "device_id": "d1", "queue": [songs[1].id, songs[2].id]},
        headers=auth_headers,
    )

    first = await client.post("/api/player/next", headers=auth_headers)
    assert first.status_code == 200, first.text
    body = first.json()["data"]
    assert body["song_id"] == songs[1].id
    assert body["queue"] == [songs[2].id]

    second = await client.post("/api/player/next", headers=auth_headers)
    body = second.json()["data"]
    assert body["song_id"] == songs[2].id
    assert body["queue"] == []

    # Queue exhausted: autoplay continues the session, but it must move to a
    # different track -- repeating the last one was the original defect.
    third = await client.post("/api/player/next", headers=auth_headers)
    body = third.json()["data"]
    assert body["state"] == "playing"
    assert body["song_id"] != songs[2].id


@pytest.mark.asyncio
async def test_next_stops_when_there_is_nothing_left_to_autoplay(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Autoplay must not invent a track: one song in the catalogue means stop."""
    songs = await seed_songs(db_session, 1)
    await client.post(
        "/api/player/play",
        json={"song_id": songs[0].id, "device_id": "d1", "queue": []},
        headers=auth_headers,
    )

    res = await client.post("/api/player/next", headers=auth_headers)
    assert res.status_code == 200, res.text
    body = res.json()["data"]
    assert body["state"] == "stopped"
    assert body["position_seconds"] == 0.0


@pytest.mark.asyncio
async def test_next_on_empty_queue_with_repeat_one_restarts(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    songs = await seed_songs(db_session, 1)
    await client.post(
        "/api/player/play",
        json={"song_id": songs[0].id, "device_id": "d1", "queue": []},
        headers=auth_headers,
    )
    await client.post(
        "/api/player/sync",
        json={"device_id": "d1", "song_id": songs[0].id, "repeat_mode": "one", "duration_seconds": 200.0},
        headers=auth_headers,
    )
    res = await client.post("/api/player/next", headers=auth_headers)
    body = res.json()["data"]
    assert body["song_id"] == songs[0].id
    assert body["state"] == "playing"
    assert body["position_seconds"] == 0.0


@pytest.mark.asyncio
async def test_previous_restarts_current_track_past_the_threshold(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    songs = await seed_songs(db_session, 2)
    await client.post(
        "/api/player/play", json={"song_id": songs[0].id, "device_id": "d1"}, headers=auth_headers
    )
    await client.post(
        "/api/player/seek",
        json={"device_id": "d1", "position_seconds": PREVIOUS_RESTART_THRESHOLD_SECONDS + 30},
        headers=auth_headers,
    )
    res = await client.post("/api/player/previous", headers=auth_headers)
    body = res.json()["data"]
    assert body["song_id"] == songs[0].id
    assert body["position_seconds"] == 0.0


@pytest.mark.asyncio
async def test_previous_steps_back_to_the_prior_song(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """`previous` used to only ever reset the position, never change track."""
    songs = await seed_songs(db_session, 2)
    await client.post(
        "/api/player/play",
        json={"song_id": songs[0].id, "device_id": "d1", "queue": [songs[1].id]},
        headers=auth_headers,
    )
    # Listen most of the way through, then advance -- this writes history.
    await client.post(
        "/api/player/seek", json={"device_id": "d1", "position_seconds": 180.0}, headers=auth_headers
    )
    advanced = await client.post("/api/player/next", headers=auth_headers)
    assert advanced.json()["data"]["song_id"] == songs[1].id

    back = await client.post("/api/player/previous", headers=auth_headers)
    body = back.json()["data"]
    assert body["song_id"] == songs[0].id, "previous did not step back to the prior track"
    assert songs[1].id in (body["queue"] or []), "the track we left should be re-queued"


# --------------------------------------------------------------------------
# WebSocket delivery of REST-driven state changes
# --------------------------------------------------------------------------

class RecordingSocket:
    """Stands in for a Starlette WebSocket; the manager sends JSON text."""

    def __init__(self):
        self.frames = []

    async def accept(self):
        return None

    async def send_text(self, message):
        self.frames.append(json.loads(message))

    async def send_json(self, message):
        self.frames.append(message)

    def of_type(self, frame_type):
        return [f for f in self.frames if f.get("type") == frame_type]


async def db_user_id(db: AsyncSession, firebase_uid: str = "user123") -> str:
    """The socket registry keys on User.id (a UUID), not the Firebase uid."""
    from app.models.user import User

    res = await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    return res.scalar_one().id


@pytest.mark.asyncio
async def test_rest_playback_change_reaches_connected_sockets(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """
    State changes were only published to Redis, so a single-instance deployment
    delivered nothing to its own connected devices.
    """
    from app.websocket.connection_manager import manager

    songs = await seed_songs(db_session, 1)
    # Materialise the user so its id is known before the socket registers.
    assert (await client.get("/api/player/current", headers=auth_headers)).status_code == 200
    uid = await db_user_id(db_session)

    listener = RecordingSocket()
    await manager.connect(listener, uid, "listener-device")
    try:
        res = await client.post(
            "/api/player/play",
            json={"song_id": songs[0].id, "device_id": "controller-device"},
            headers=auth_headers,
        )
        assert res.status_code == 200, res.text
    finally:
        manager.disconnect(uid, "listener-device")

    updates = listener.of_type("PLAYBACK_UPDATED")
    assert updates, f"no PLAYBACK_UPDATED frame delivered; got {listener.frames}"
    assert updates[-1]["song_id"] == songs[0].id
    assert updates[-1]["state"] == "playing"


@pytest.mark.asyncio
async def test_every_rest_transition_is_broadcast(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    from app.websocket.connection_manager import manager

    songs = await seed_songs(db_session, 2)
    assert (await client.get("/api/player/current", headers=auth_headers)).status_code == 200
    uid = await db_user_id(db_session)

    listener = RecordingSocket()
    await manager.connect(listener, uid, "listener-device")
    try:
        await client.post(
            "/api/player/play",
            json={"song_id": songs[0].id, "device_id": "ctl", "queue": [songs[1].id]},
            headers=auth_headers,
        )
        await client.post("/api/player/pause", json={"device_id": "ctl", "position_seconds": 10.0}, headers=auth_headers)
        await client.post("/api/player/resume", json={"device_id": "ctl"}, headers=auth_headers)
        await client.post("/api/player/seek", json={"device_id": "ctl", "position_seconds": 30.0}, headers=auth_headers)
        await client.post("/api/player/next", headers=auth_headers)
        await client.post("/api/player/stop", headers=auth_headers)
    finally:
        manager.disconnect(uid, "listener-device")

    states = [f["state"] for f in listener.of_type("PLAYBACK_UPDATED")]
    assert len(states) == 6, f"expected one frame per transition, got {states}"
    assert states[0] == "playing"
    assert states[1] == "paused"
    assert states[-1] == "stopped"


@pytest.mark.asyncio
async def test_broadcast_carries_no_origin_instance_to_clients(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """`origin_instance` is loop-prevention plumbing, not client-facing data."""
    from app.websocket.connection_manager import manager

    songs = await seed_songs(db_session, 1)
    assert (await client.get("/api/player/current", headers=auth_headers)).status_code == 200
    uid = await db_user_id(db_session)

    listener = RecordingSocket()
    await manager.connect(listener, uid, "listener-2")
    try:
        await client.post(
            "/api/player/play",
            json={"song_id": songs[0].id, "device_id": "controller"},
            headers=auth_headers,
        )
    finally:
        manager.disconnect(uid, "listener-2")

    assert listener.of_type("PLAYBACK_UPDATED")
    for frame in listener.of_type("PLAYBACK_UPDATED"):
        assert "origin_instance" not in frame


@pytest.mark.asyncio
async def test_pubsub_skips_messages_it_published_itself():
    """Without the origin tag, a node would re-broadcast its own fan-out."""
    from app.websocket.connection_manager import manager
    from app.websocket.pubsub import INSTANCE_ID, player_pubsub

    def pmessage(origin: str) -> dict:
        return {
            "type": "pmessage",
            "pattern": "user:*:player",
            "channel": "user:loop-user:player",
            "data": json.dumps({
                "type": "PLAYBACK_UPDATED",
                "user_id": "loop-user",
                "state": "playing",
                "origin_instance": origin,
            }),
        }

    listener = RecordingSocket()
    await manager.connect(listener, "loop-user", "loop-device")
    try:
        await player_pubsub._dispatch(pmessage(INSTANCE_ID))
        assert listener.of_type("PLAYBACK_UPDATED") == [], "own message was re-delivered"

        await player_pubsub._dispatch(pmessage("some-other-node"))
        assert len(listener.of_type("PLAYBACK_UPDATED")) == 1, "peer message was dropped"
    finally:
        manager.disconnect("loop-user", "loop-device")


@pytest.mark.asyncio
async def test_pubsub_recovers_user_id_from_the_channel_name():
    from app.websocket.connection_manager import manager
    from app.websocket.pubsub import player_pubsub

    listener = RecordingSocket()
    await manager.connect(listener, "chan-user", "chan-device")
    try:
        await player_pubsub._dispatch({
            "type": "pmessage",
            "pattern": "user:*:player",
            "channel": "user:chan-user:player",
            "data": json.dumps({"type": "PLAYBACK_UPDATED", "state": "paused"}),
        })
        assert len(listener.of_type("PLAYBACK_UPDATED")) == 1
    finally:
        manager.disconnect("chan-user", "chan-device")


# --------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_heartbeat_works_without_a_body(client: AsyncClient, auth_headers: dict):
    """The README documents a bare ping; the body used to be required."""
    reg = await client.post(
        "/api/devices/register",
        json={"device_id": "hb-dev-1", "device_name": "Test Phone", "device_type": "mobile", "platform": "android"},
        headers=auth_headers,
    )
    assert reg.status_code == 201, reg.text

    res = await client.post("/api/devices/hb-dev-1/heartbeat", headers=auth_headers)
    assert res.status_code == 200, res.text
    assert res.json()["data"]["is_online"] is True


@pytest.mark.asyncio
async def test_heartbeat_still_accepts_a_body(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/api/devices/register",
        json={"device_id": "hb-dev-2", "device_name": "Test Tablet", "device_type": "tablet", "platform": "ios"},
        headers=auth_headers,
    )
    res = await client.post(
        "/api/devices/hb-dev-2/heartbeat",
        json={"is_online": True, "app_version": "1.2.3"},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_heartbeat_for_another_users_device_is_404(
    client: AsyncClient, auth_headers: dict, auth_headers_user2: dict
):
    await client.post(
        "/api/devices/register",
        json={"device_id": "alice-only", "device_name": "Alice Phone", "device_type": "mobile", "platform": "android"},
        headers=auth_headers,
    )
    res = await client.post("/api/devices/alice-only/heartbeat", headers=auth_headers_user2)
    assert res.status_code == 404


# --------------------------------------------------------------------------
# Cache key agreement
# --------------------------------------------------------------------------

def test_producers_and_invalidators_share_one_cache_key_builder():
    """
    The home feed was cached under one key and invalidated under another, so the
    cache was never read. Import identity is the drift guard. (`from app.services
    import history_service` resolves to the singleton, not the module, hence
    import_module.)
    """
    from importlib import import_module
    from app.utils.cache_keys import home_recommendations_key

    users = [
        "app.services.history_service",
        "app.services.library_service",
        "app.services.recommendation_service",
        "app.workers.recommendation_worker",
    ]
    for name in users:
        module = import_module(name)
        assert getattr(module, "home_recommendations_key", None) is home_recommendations_key, name

    assert home_recommendations_key("u1") == "recommendations:user:u1"


def test_playback_service_uses_shared_playback_keys():
    import fnmatch
    from importlib import import_module
    from app.utils.cache_keys import playback_state_key, player_channel
    from app.websocket.pubsub import PLAYER_CHANNEL_PATTERN

    ps = import_module("app.services.playback_service")
    assert ps.playback_state_key is playback_state_key
    assert ps.player_channel is player_channel
    # The subscriber pattern must actually match what the publisher emits.
    assert fnmatch.fnmatch(player_channel("abc123"), PLAYER_CHANNEL_PATTERN)


# --------------------------------------------------------------------------
# Legacy Gaana stream-link decryption
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["", "x", "not-base64-at-all", "0", "9abc", "   "])
async def test_decrypt_link_fails_soft(payload: str):
    """Malformed payloads must return "" rather than raising."""
    from api.functions import Functions

    assert await Functions().decryptLink(payload) == ""


@pytest.mark.asyncio
async def test_decrypt_link_logs_instead_of_failing_silently():
    """
    A rotated Gaana master key would otherwise show up only as empty stream
    URLs, with nothing in the logs to explain it.
    """
    import logging
    from api.functions import Functions

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("gaana.functions")
    handler = Capture()
    logger.addHandler(handler)
    try:
        assert await Functions().decryptLink("not-base64-at-all") == ""
    finally:
        logger.removeHandler(handler)

    assert records, "decryptLink swallowed the failure without logging"
    assert records[0].levelno >= logging.WARNING
    assert "decryption failed" in records[0].getMessage().lower()


# --------------------------------------------------------------------------
# Error responses do not leak internals
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_error_envelope_shape_is_stable(client: AsyncClient):
    res = await client.get("/api/playlists/nope-not-real")
    assert res.status_code == 404
    body = res.json()
    assert body["success"] is False
    assert set(body["error"]) >= {"code", "message"}


# --------------------------------------------------------------------------
# Non-UUID identifiers must 404, never 500 (SQLite hid this; Postgres does not)
#
# On PostgreSQL a GUID column is a native `uuid`, so binding a client string
# like "does-not-exist" against it raised at bind time and surfaced as a 500.
# SQLite stores GUIDs as text and compares them fine, so the whole class of bug
# was invisible until the suite ran on Postgres. These tests pin the coercion
# helper and the endpoint behaviour so they hold on either backend.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    ["does-not-exist", "simtaangaran", "", "123", "not-a-uuid", "nope-not-real"],
)
def test_as_uuid_returns_none_for_non_uuid(value):
    from app.db.base import as_uuid, is_uuid

    assert as_uuid(value) is None
    assert is_uuid(value) is False


def test_as_uuid_accepts_real_uuids_and_uuid_objects():
    import uuid as _uuid
    from app.db.base import as_uuid, is_uuid

    u = _uuid.uuid4()
    assert is_uuid(str(u)) is True
    assert as_uuid(str(u)) == u
    # An actual UUID instance passes through unchanged.
    assert as_uuid(u) == u
    # None is "no identifier", not an error.
    assert as_uuid(None) is None


def test_guid_bind_rejects_non_uuid_on_postgres_with_a_named_error():
    """The bind guard is the backstop for any call site that forgets to screen.

    It must raise a message that names the fix, not the driver's opaque
    "badly formed hexadecimal UUID string".
    """
    from sqlalchemy.dialects import postgresql
    from app.db.base import GUID

    impl = GUID()
    dialect = postgresql.dialect()
    with pytest.raises(ValueError, match="as_uuid"):
        impl.process_bind_param("does-not-exist", dialect)


@pytest.mark.asyncio
async def test_like_unknown_song_is_404_not_500(client: AsyncClient, auth_headers: dict, monkeypatch):
    from app.services.catalog_service import catalog_service

    async def no_upstream_match(_ids):
        return []

    monkeypatch.setattr(catalog_service.gaana, "get_track_info", no_upstream_match)

    res = await client.post("/api/songs/does-not-exist/like", headers=auth_headers)
    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == "SONG_NOT_FOUND"


@pytest.mark.asyncio
async def test_save_non_uuid_album_is_404_not_500(client: AsyncClient, auth_headers: dict):
    # album_id is an unresolved path value bound straight to a uuid column.
    res = await client.post("/api/albums/not-a-real-album/save", headers=auth_headers)
    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == "ALBUM_NOT_FOUND"


@pytest.mark.asyncio
async def test_follow_non_uuid_artist_is_404_not_500(client: AsyncClient, auth_headers: dict):
    res = await client.post("/api/artists/not-a-real-artist/follow", headers=auth_headers)
    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == "ARTIST_NOT_FOUND"


@pytest.mark.asyncio
async def test_sync_with_non_uuid_ids_does_not_500(client: AsyncClient, auth_headers: dict, monkeypatch):
    """A client reporting a non-uuid song_id/playlist_id must not crash sync."""
    from app.services.catalog_service import catalog_service

    async def no_upstream_match(_ids):
        return []

    monkeypatch.setattr(catalog_service.gaana, "get_track_info", no_upstream_match)

    res = await client.post(
        "/api/player/sync",
        json={"device_id": "d1", "song_id": "does-not-exist", "playlist_id": "nope"},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    # Unresolvable ids reconcile to "no song / no playlist", not an error.
    assert data["song_id"] is None
    assert data["playlist_id"] is None


@pytest.mark.asyncio
async def test_remove_non_uuid_song_from_playlist_does_not_500(
    client: AsyncClient, auth_headers: dict
):
    playlist_id = await make_playlist(client, auth_headers, title="Remove Junk Id")
    res = await client.delete(
        f"/api/playlists/{playlist_id}/songs/not-a-uuid", headers=auth_headers
    )
    # Nothing to remove, but it must be a clean response rather than a 500.
    assert res.status_code == 200, res.text



# --- Redis credential / fallback handling -----------------------------------
# Production logged "invalid username-password pair" on every cache call and on
# a 5s pub/sub reconnect loop: a rejected credential was treated as a transient
# fault forever, and the Upstash REST pair silently overrode a working
# REDIS_URL.


def test_explicit_redis_url_wins_over_upstash_rest_pair():
    """
    UPSTASH_REDIS_REST_TOKEN authenticates the HTTP REST API, so deriving a
    rediss:// password from it can be rejected at AUTH. An explicitly set
    REDIS_URL must not be overridden by it.
    """
    s = Settings(
        REDIS_URL="rediss://default:real-resp-password@db.upstash.io:6379",
        UPSTASH_REDIS_REST_URL="https://db.upstash.io",
        UPSTASH_REDIS_REST_TOKEN="a-rest-api-token",
    )
    assert s.get_active_redis_url() == "rediss://default:real-resp-password@db.upstash.io:6379"
    assert s.redis_url_source() == "REDIS_URL"


def test_upstash_rest_pair_is_used_only_without_an_explicit_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    s = Settings(
        _env_file=None,
        UPSTASH_REDIS_REST_URL="https://db.upstash.io",
        UPSTASH_REDIS_REST_TOKEN="a-rest-api-token",
    )
    assert s.get_active_redis_url() == "rediss://default:a-rest-api-token@db.upstash.io:6379"
    assert "derived" in s.redis_url_source()


def test_placeholder_redis_password_is_named_before_it_causes_an_auth_error():
    """
    scripts/gen_render_env.py emits ROTATED_UPSTASH_TOKEN as a stand-in. Uploaded
    as-is it only shows up as "invalid username-password pair", which reads like
    a rotation problem rather than an unfinished env file.
    """
    s = Settings(
        _env_file=None,
        REDIS_URL="rediss://default:ROTATED_UPSTASH_TOKEN@model-dog-84669.upstash.io:6379",
    )
    assert s.redis_url_placeholder() == "ROTATED_UPSTASH_TOKEN"
    assert Settings(
        _env_file=None, REDIS_URL="rediss://default:a-real-secret@db.upstash.io:6379"
    ).redis_url_placeholder() is None


@pytest.mark.parametrize(
    "raw",
    [
        '"rediss://default:pw@db.upstash.io:6379"',
        "'rediss://default:pw@db.upstash.io:6379'",
        "  rediss://default:pw@db.upstash.io:6379\n",
    ],
)
def test_redis_url_quotes_and_whitespace_are_stripped(raw: str):
    """
    The password is sent verbatim, so a quote or newline picked up from a
    dashboard paste comes back as an auth failure rather than a parse error.
    """
    assert Settings(REDIS_URL=raw).REDIS_URL == "rediss://default:pw@db.upstash.io:6379"


def test_redact_url_never_echoes_the_password():
    from app.config.settings import redact_url

    redacted = redact_url("rediss://default:sup3r-s3cret@db.upstash.io:6379")
    assert "sup3r-s3cret" not in redacted
    assert redacted == "rediss://default:***@db.upstash.io:6379"
    # A password-less URL stays readable, and no input may raise.
    assert redact_url("redis://localhost:6379/0") == "redis://localhost:6379/0"
    assert redact_url(None) == "<unset>"


@pytest.mark.asyncio
async def test_rejected_credentials_stop_redis_for_the_process(monkeypatch):
    """
    A bad password cannot succeed on retry. It must latch off, so cache calls
    stop rebuilding a client (and stop paying the connect timeout) on every
    request, and the in-memory fallback keeps serving.
    """
    import importlib

    # app.services re-exports the cache_service *instance*, shadowing the
    # submodule name, so reach the module through importlib.
    cache_module = importlib.import_module("app.services.cache_service")
    svc = cache_module.CacheService()
    monkeypatch.setattr(settings, "REDIS_ENABLED", True)

    created = []
    monkeypatch.setattr(
        cache_module.aioredis,
        "from_url",
        lambda *a, **kw: created.append(1) or object(),
    )

    svc.note_failure(Exception("invalid username-password pair"), "startup ping")

    assert svc.disabled_reason is not None
    assert svc.is_connected is False
    assert await svc.get_client() is None
    assert created == [], "no further connection may be attempted after a rejected credential"

    await svc.set_json("k", {"v": 1}, ttl_seconds=60)
    assert await svc.get_json("k") == {"v": 1}


@pytest.mark.asyncio
async def test_transient_redis_failure_is_retried_not_latched(monkeypatch):
    """A refused connection is transient: it must cool down, not disable Redis."""
    import importlib

    cache_module = importlib.import_module("app.services.cache_service")
    svc = cache_module.CacheService()
    monkeypatch.setattr(settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(cache_module.aioredis, "from_url", lambda *a, **kw: object())

    svc.note_failure(ConnectionError("Connection refused"), "get")
    assert svc.disabled_reason is None
    # Inside the cooldown nothing is dialed; once it lapses, reconnects resume.
    assert await svc.get_client() is None
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: svc._retry_after + 1)
    assert await svc.get_client() is not None
