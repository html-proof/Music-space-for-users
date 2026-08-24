"""
Radio stations and queue-exhaustion autoplay.

Everything here runs with REDIS_ENABLED off, so the station lives in
cache_service's in-memory fallback -- which is exactly the degraded mode a free
Render deployment without Upstash runs in.

Station tracks come from Gaana, never from a `songs` scan, so `seed_catalog`
seeds *Gaana* rather than the database: it registers raw track payloads that the
stubbed client returns, and lets the normal upsert path create the rows. The
`Song` objects it hands back therefore have the same ids the endpoint will
return, which is what the id assertions below rely on -- but nothing in the
service reaches those rows except through Gaana.
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


#: Raw Gaana track payloads the stubbed client will serve. Populated by
#: `seed_catalog`, cleared per test by the `gaana_upstream` fixture.
_UPSTREAM_TRACKS: list = []


def _raw_track(song: Song) -> dict:
    """The Gaana payload a seeded row corresponds to.

    Keyed by the same `seokey`, so `upsert_gaana_song` matches the existing row
    and hands back the same id rather than creating a duplicate.
    """
    return {
        "seokey": song.external_id,
        "track_id": song.external_id,
        "title": song.title,
        "artists": song.artist_name,
        "album": song.album_name or "Single",
        "duration": str(song.duration or 0),
        "language": song.language or "English",
        "genres": song.genre or "",
        "mood": song.mood or "",
        "is_explicit": False,
        "images": {"urls": {}},
        "stream_urls": {"urls": {}},
    }


#: Field groups a query is matched against, mirroring how a real search behaves:
#: all tokens have to land in the *same* group, so "Radio Artist" matches the
#: artist credit rather than any track whose title happens to contain "Radio"
#: and whose credit happens to contain "Artist".
_MATCH_GROUPS = (("artists",), ("title", "album"), ("genres", "mood", "language"))


def _matches(track: dict, query: str) -> bool:
    tokens = [t for t in (query or "").lower().split() if t]
    if not tokens:
        return False
    for group in _MATCH_GROUPS:
        haystack = " ".join(str(track.get(field) or "") for field in group).lower()
        if all(token in haystack for token in tokens):
            return True
    return False


@pytest.fixture(autouse=True)
def gaana_upstream(monkeypatch):
    """
    Gaana, stubbed to serve exactly what `seed_catalog` registered.

    This replaces a fixture that made every upstream call fail, back when the
    station was really being built from a `select(Song)` scan and upstream was
    only ever an optional widening step. Now that Gaana is the only source, a
    dead upstream means an empty station -- which is what
    `test_dead_upstream_cannot_start_a_station` asserts, deliberately, on its
    own.
    """
    _UPSTREAM_TRACKS.clear()

    async def search_songs(query, limit=10, *args, **kwargs):
        matched = [t for t in _UPSTREAM_TRACKS if _matches(t, query)]
        return matched[:limit] if matched else {"error": "no results"}

    async def get_trending(language="English", limit=10, *args, **kwargs):
        return list(_UPSTREAM_TRACKS)[:limit] or {"error": "no results"}

    async def get_new_releases(language="English", limit=10, *args, **kwargs):
        return {"tracks": list(_UPSTREAM_TRACKS)[:limit]}

    async def get_track_info(seokeys, *args, **kwargs):
        wanted = set(seokeys or [])
        return [t for t in _UPSTREAM_TRACKS if t["seokey"] in wanted] or {"error": "no results"}

    async def get_top_tracks(*args, **kwargs):
        return {"error": "no results"}

    monkeypatch.setattr(catalog_service.gaana, "search_songs", search_songs)
    monkeypatch.setattr(catalog_service.gaana, "get_trending", get_trending)
    monkeypatch.setattr(catalog_service.gaana, "get_new_releases", get_new_releases)
    monkeypatch.setattr(catalog_service.gaana, "get_track_info", get_track_info)
    monkeypatch.setattr(catalog_service.gaana, "get_top_tracks", get_top_tracks)
    yield
    _UPSTREAM_TRACKS.clear()


@pytest.fixture
def dead_upstream(monkeypatch):
    """Gaana unreachable: every entry point returns the no-results shape."""
    async def failed(*args, **kwargs):
        return {"error": "no results"}

    for method in ("search_songs", "get_trending", "get_new_releases",
                   "get_track_info", "get_top_tracks"):
        monkeypatch.setattr(catalog_service.gaana, method, failed)


async def seed_catalog(db: AsyncSession, n: int = 12, artist_name: str = "Radio Artist"):
    """A Gaana catalogue big enough that a 20-track batch has room to work with.

    Rows are written first so the test has stable ids to assert on; the same
    tracks are then registered upstream, which is where the service actually
    reads them from.
    """
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
    _UPSTREAM_TRACKS.extend(_raw_track(s) for s in songs)
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
    await db_session.refresh(song)
    _UPSTREAM_TRACKS.append(_raw_track(song))

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

    The artist case is now resolved against Gaana rather than against a local
    `songs` LIKE scan, so "nobody-by-that-name" is rejected because Gaana has
    nothing for it -- not because we happen not to have ingested it.
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
    """Nothing registered upstream, so Gaana has nothing to build a station
    from and the endpoint says so."""
    res = await client.post("/api/player/radio", json={}, headers=auth_headers)
    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == "RADIO_UNAVAILABLE"


@pytest.mark.asyncio
async def test_dead_upstream_cannot_start_a_station(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, dead_upstream
):
    """Rows in `songs` must not rescue a station when Gaana is unreachable.

    A full local catalogue is seeded and Gaana is then made to fail. The old
    implementation answered from `SELECT ... ORDER BY play_count`, so the
    station played on and every listener converged on the same locally-popular
    tracks; the honest answer is that the station cannot start.
    """
    for i in range(12):
        db_session.add(Song(
            external_id=f"stale-{i}", title=f"Stale {i}", artist_name="Stale Artist",
            duration=200, genre="Pop", language="English", play_count=100 + i,
        ))
    await db_session.commit()

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
