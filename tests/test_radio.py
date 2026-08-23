"""
Radio stations and queue-exhaustion autoplay.

Everything here runs with REDIS_ENABLED off, so the station lives in
cache_service's in-memory fallback -- which is exactly the degraded mode a free
Render deployment without Upstash runs in.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.song import Artist, Song
from app.models.user import User
from app.services.catalog_service import catalog_service
from app.services.radio_service import RadioService


async def user_id_for(db: AsyncSession, firebase_uid: str) -> str:
    """
    Stations are keyed by the User row's id, not the Firebase uid in the bearer
    token, so tests have to resolve it the same way the request does.
    """
    res = await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    user = res.scalars().first()
    assert user is not None, f"no user row for {firebase_uid}"
    return user.id


async def make_user(db: AsyncSession, firebase_uid: str = "direct-caller") -> str:
    """
    A real user row for tests that call RadioService directly. user_id lands on
    native uuid columns, so a placeholder string is rejected on PostgreSQL.
    """
    user = User(firebase_uid=firebase_uid, email=f"{firebase_uid}@example.test")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user.id


@pytest.fixture(autouse=True)
def no_upstream(monkeypatch):
    """
    Starting a station is allowed one upstream call. Tests must not make it, so
    every network entry point radio touches returns the library's own
    no-results shape.
    """
    async def empty_tracks(*args, **kwargs):
        return {"error": "no results"}

    async def empty_list(*args, **kwargs):
        return {"error": "no results"}

    monkeypatch.setattr(catalog_service.gaana, "get_top_tracks", empty_tracks)
    monkeypatch.setattr(catalog_service.gaana, "get_trending", empty_list)
    monkeypatch.setattr(catalog_service.gaana, "get_track_info", empty_list)


async def seed_catalog(db: AsyncSession, n: int = 12, artist_name: str = "Radio Artist"):
    """A catalogue big enough that a 20-track batch has room to work with."""
    songs = [
        Song(
            external_id=f"radio-s{i}",
            title=f"Radio Song {i}",
            artist_name=artist_name if i % 2 == 0 else f"Other Artist {i}",
            duration=180 + i,
            genre="Pop" if i % 2 == 0 else "Rock",
            mood="Happy" if i % 3 == 0 else "Chill",
            language="English",
            play_count=i,
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
# Starting a station
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_personalized_radio_starts_without_a_seed(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    await seed_catalog(db_session)

    res = await client.post("/api/player/radio", json={}, headers=auth_headers)
    assert res.status_code == 200, res.text
    body = res.json()["data"]
    assert body["state"] == "playing"
    assert body["song_id"]
    # The station is endless, so a queue has to come back with it.
    assert len(body["queue"]) > 0
    assert body["song_id"] not in body["queue"]


@pytest.mark.asyncio
async def test_song_seeded_radio_excludes_nothing_but_plays_related_tracks(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    songs = await seed_catalog(db_session)

    res = await client.post(
        "/api/player/radio",
        json={"seed_type": "song", "seed_id": songs[0].id},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()["data"]
    assert body["state"] == "playing"
    # Same genre as the seed, so the batch should be drawn from that pool.
    picked = {body["song_id"], *body["queue"]}
    pop_ids = {s.id for s in songs if s.genre == "Pop"}
    assert picked & pop_ids


@pytest.mark.asyncio
async def test_artist_seeded_radio_accepts_a_plain_artist_name(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """
    Only songs carry the name here -- there is no Artist row. That is how
    multi-artist credit strings arrive, and it used to 404.
    """
    songs = await seed_catalog(db_session, n=40)
    by_artist = {s.id for s in songs if s.artist_name == "Radio Artist"}

    res = await client.post(
        "/api/player/radio",
        json={"seed_type": "artist", "seed_id": "Radio Artist"},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()["data"]
    assert body["state"] == "playing"
    assert {body["song_id"], *body["queue"]} & by_artist


@pytest.mark.asyncio
async def test_artist_seeded_batch_prefers_that_artist(db_session: AsyncSession):
    """
    With enough of the artist's own catalogue, a batch should need no padding --
    otherwise "artist radio" is just shuffle.
    """
    songs = await seed_catalog(db_session, n=40)
    by_artist = {s.id for s in songs if s.artist_name == "Radio Artist"}
    assert len(by_artist) >= 6
    user_id = await make_user(db_session)

    batch = await RadioService.build_batch(
        db_session, user_id=user_id, seed_type="artist", seed_id="Radio Artist", limit=6
    )
    assert len(batch) == 6
    assert {s.id for s in batch} <= by_artist


@pytest.mark.asyncio
async def test_artist_seeded_radio_resolves_a_stored_artist_row(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    artist = Artist(external_id="gaana-artist-9", name="Seeded Artist", seokey="seeded-artist")
    db_session.add(artist)
    await db_session.commit()
    await db_session.refresh(artist)

    song = Song(
        external_id="radio-artist-track",
        title="Artist Track",
        artist_id=artist.id,
        artist_name="Seeded Artist",
        duration=210,
    )
    db_session.add(song)
    await db_session.commit()

    # Every identifier a client might plausibly hold for the same artist.
    for seed in (artist.id, "gaana-artist-9", "seeded-artist", "Seeded Artist"):
        res = await client.post(
            "/api/player/radio",
            json={"seed_type": "artist", "seed_id": seed},
            headers=auth_headers,
        )
        assert res.status_code == 200, f"{seed}: {res.text}"


@pytest.mark.asyncio
async def test_mood_seeded_radio_starts(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    await seed_catalog(db_session)

    res = await client.post(
        "/api/player/radio",
        json={"seed_type": "mood", "seed_id": "Happy"},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["state"] == "playing"


@pytest.mark.asyncio
async def test_radio_drops_stale_playlist_context(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """A station is not a playlist; leaving playlist_id set would mislabel it."""
    songs = await seed_catalog(db_session)
    await client.post(
        "/api/player/play",
        json={
            "song_id": songs[0].id,
            "playlist_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        },
        headers=auth_headers,
    )

    res = await client.post("/api/player/radio", json={}, headers=auth_headers)
    assert res.status_code == 200, res.text
    assert res.json()["data"]["playlist_id"] is None


# --------------------------------------------------------------------------
# Seeds that cannot be resolved
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_seed_is_404_not_500(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """
    Seed ids come straight from the request body. A non-uuid one used to reach a
    native uuid column and 500; it has to be a clean client error.
    """
    await seed_catalog(db_session)

    for seed_type, seed_id in (
        ("song", "not-a-uuid-and-not-a-seokey"),
        ("artist", "nobody-by-that-name"),
        ("song", "3f2504e0-4f89-11d3-9a0c-0305e82c3399"),
    ):
        res = await client.post(
            "/api/player/radio",
            json={"seed_type": seed_type, "seed_id": seed_id},
            headers=auth_headers,
        )
        assert res.status_code == 404, f"{seed_type}/{seed_id}: {res.status_code} {res.text}"
        assert res.json()["error"]["code"] == "SEED_NOT_FOUND"


@pytest.mark.asyncio
async def test_seed_id_is_required_for_seeded_types(
    client: AsyncClient, auth_headers: dict
):
    for seed_type in ("song", "artist", "mood"):
        res = await client.post(
            "/api/player/radio", json={"seed_type": seed_type}, headers=auth_headers
        )
        assert res.status_code == 422, f"{seed_type}: {res.status_code}"

    blank = await client.post(
        "/api/player/radio",
        json={"seed_type": "mood", "seed_id": "   "},
        headers=auth_headers,
    )
    assert blank.status_code == 422


@pytest.mark.asyncio
async def test_unknown_seed_type_is_rejected(client: AsyncClient, auth_headers: dict):
    res = await client.post(
        "/api/player/radio",
        json={"seed_type": "podcast", "seed_id": "x"},
        headers=auth_headers,
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_empty_catalogue_cannot_start_a_station(
    client: AsyncClient, auth_headers: dict
):
    res = await client.post("/api/player/radio", json={}, headers=auth_headers)
    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == "RADIO_UNAVAILABLE"


@pytest.mark.asyncio
async def test_radio_requires_authentication(client: AsyncClient):
    res = await client.post("/api/player/radio", json={})
    assert res.status_code in (401, 403)


# --------------------------------------------------------------------------
# Autoplay
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_station_refills_when_its_queue_runs_out(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """The point of a station: it does not end when the served batch does."""
    await seed_catalog(db_session, n=40)

    start = await client.post("/api/player/radio", json={}, headers=auth_headers)
    queue_length = len(start.json()["data"]["queue"])
    assert queue_length > 0

    # Drain the batch exactly.
    for _ in range(queue_length):
        res = await client.post("/api/player/next", headers=auth_headers)
        assert res.status_code == 200, res.text
    assert res.json()["data"]["queue"] == []

    refilled = await client.post("/api/player/next", headers=auth_headers)
    assert refilled.status_code == 200, refilled.text
    body = refilled.json()["data"]
    assert body["state"] == "playing"
    assert len(body["queue"]) > 0


@pytest.mark.asyncio
async def test_refill_avoids_tracks_the_station_already_served(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    await seed_catalog(db_session, n=40)
    await client.post("/api/player/radio", json={}, headers=auth_headers)
    user_id = await user_id_for(db_session, "user123")

    station = await RadioService.get_station(user_id)
    assert station is not None
    served = set(station["served"])

    batch = await RadioService.build_batch(
        db_session,
        user_id=user_id,
        seed_type=station["seed_type"],
        seed_id=station.get("seed_id"),
        exclude_ids=served,
        limit=10,
    )
    assert batch, "a 40-track catalogue should still have unserved songs"
    assert not {s.id for s in batch} & served


@pytest.mark.asyncio
async def test_stop_ends_the_station(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Without clearing the station, Stop would be undone by the next skip."""
    await seed_catalog(db_session, n=40)
    await client.post("/api/player/radio", json={}, headers=auth_headers)
    user_id = await user_id_for(db_session, "user123")
    assert await RadioService.get_station(user_id) is not None

    await client.post("/api/player/stop", headers=auth_headers)
    assert await RadioService.get_station(user_id) is None


@pytest.mark.asyncio
async def test_playing_something_unrelated_leaves_the_station(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    songs = await seed_catalog(db_session, n=40)
    await client.post("/api/player/radio", json={}, headers=auth_headers)
    user_id = await user_id_for(db_session, "user123")

    state = (await client.get("/api/player/current", headers=auth_headers)).json()["data"]
    off_station = next(
        s for s in songs if s.id != state["song_id"] and s.id not in state["queue"]
    )
    await client.post(
        "/api/player/play", json={"song_id": off_station.id}, headers=auth_headers
    )
    assert await RadioService.get_station(user_id) is None


@pytest.mark.asyncio
async def test_playing_a_track_from_the_station_keeps_it(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """
    Clients start a station and then call /play on its first track. Treating
    that as leaving would kill the station one request after creating it.
    """
    await seed_catalog(db_session, n=40)
    start = await client.post("/api/player/radio", json={}, headers=auth_headers)
    body = start.json()["data"]
    user_id = await user_id_for(db_session, "user123")

    await client.post(
        "/api/player/play", json={"song_id": body["song_id"]}, headers=auth_headers
    )
    assert await RadioService.get_station(user_id) is not None

    await client.post(
        "/api/player/play", json={"song_id": body["queue"][0]}, headers=auth_headers
    )
    assert await RadioService.get_station(user_id) is not None


@pytest.mark.asyncio
async def test_an_explicit_queue_always_leaves_the_station(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    songs = await seed_catalog(db_session, n=40)
    start = await client.post("/api/player/radio", json={}, headers=auth_headers)
    first = start.json()["data"]["song_id"]
    user_id = await user_id_for(db_session, "user123")

    # Same track, but the client supplied its own queue: that is a new context.
    await client.post(
        "/api/player/play",
        json={"song_id": first, "queue": [songs[1].id]},
        headers=auth_headers,
    )
    assert await RadioService.get_station(user_id) is None


@pytest.mark.asyncio
async def test_stations_are_per_user(
    client: AsyncClient, auth_headers: dict, auth_headers_user2: dict, db_session: AsyncSession
):
    await seed_catalog(db_session, n=40)
    await client.post("/api/player/radio", json={}, headers=auth_headers)
    await client.post("/api/player/radio", json={}, headers=auth_headers_user2)
    one = await user_id_for(db_session, "user123")
    two = await user_id_for(db_session, "user456")

    await client.post("/api/player/stop", headers=auth_headers)
    assert await RadioService.get_station(one) is None
    assert await RadioService.get_station(two) is not None


@pytest.mark.asyncio
async def test_repeat_one_wins_over_autoplay(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    songs = await seed_catalog(db_session, n=40)
    await client.post(
        "/api/player/play",
        json={"song_id": songs[0].id, "device_id": "d1", "queue": []},
        headers=auth_headers,
    )
    await client.post(
        "/api/player/sync",
        json={
            "device_id": "d1",
            "song_id": songs[0].id,
            "repeat_mode": "one",
            "duration_seconds": 200.0,
        },
        headers=auth_headers,
    )

    res = await client.post("/api/player/next", headers=auth_headers)
    body = res.json()["data"]
    assert body["song_id"] == songs[0].id
    assert body["state"] == "playing"


# --------------------------------------------------------------------------
# Batch construction
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_batch_never_repeats_a_track_within_a_batch(db_session: AsyncSession):
    await seed_catalog(db_session, n=40)
    user_id = await make_user(db_session)
    batch = await RadioService.build_batch(
        db_session, user_id=user_id, seed_type="personalized", limit=20
    )
    ids = [s.id for s in batch]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_build_batch_respects_the_limit(db_session: AsyncSession):
    await seed_catalog(db_session, n=40)
    user_id = await make_user(db_session)
    batch = await RadioService.build_batch(
        db_session, user_id=user_id, seed_type="personalized", limit=5
    )
    assert len(batch) == 5


@pytest.mark.asyncio
async def test_build_batch_falls_back_to_personalized_on_a_bad_seed_type(
    db_session: AsyncSession,
):
    await seed_catalog(db_session, n=40)
    user_id = await make_user(db_session)
    batch = await RadioService.build_batch(
        db_session, user_id=user_id, seed_type="nonsense", limit=5
    )
    assert batch


@pytest.mark.asyncio
async def test_set_station_bounds_what_it_remembers(db_session: AsyncSession):
    """Served ids must not grow without limit as a listener keeps skipping."""
    from app.services.radio_service import MAX_SERVED_REMEMBERED

    station = await RadioService.set_station(
        "user123", "personalized", None, served=[f"s{i}" for i in range(500)]
    )
    assert len(station["served"]) == MAX_SERVED_REMEMBERED
    # The most recent ids are the ones worth not repeating.
    assert station["served"][-1] == "s499"


@pytest.mark.asyncio
async def test_set_station_deduplicates_served_ids(db_session: AsyncSession):
    station = await RadioService.set_station(
        "user123", "song", "seed", served=["a", "b", "a", "c", "b"]
    )
    assert station["served"] == ["a", "b", "c"]
