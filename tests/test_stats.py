"""
Listening statistics: top tracks, top artists, and the summary rollup.

History rows are written directly with explicit `started_at` values so the range
windows are actually exercised -- driving them through the player would put
every row at "now" and make 4weeks, 6months and all indistinguishable.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.history import ListeningHistory
from app.models.song import Song
from app.models.user import User
from app.services.stats_service import StatsService


async def register(client: AsyncClient, headers: dict, db: AsyncSession) -> str:
    """
    Create the user row the way a real request does, then hand back its id --
    history rows carry a foreign key to it.
    """
    res = await client.get("/api/me/stats", headers=headers)
    assert res.status_code == 200, res.text
    uid = headers["Authorization"].removeprefix("Bearer test_token_")
    row = await db.execute(select(User).where(User.firebase_uid == uid))
    user = row.scalars().first()
    assert user is not None
    return user.id


async def seed_songs(db: AsyncSession):
    songs = [
        Song(
            external_id="stats-a",
            title="Alpha",
            artist_name="Artist One",
            duration=200,
            genre="Pop",
            language="English",
            mood="Happy",
        ),
        Song(
            external_id="stats-b",
            title="Beta",
            artist_name="Artist One",
            duration=200,
            genre="Pop",
            language="English",
            mood="Chill",
        ),
        Song(
            external_id="stats-c",
            title="Gamma",
            artist_name="Artist Two",
            duration=200,
            genre="Rock",
            language="Hindi",
            mood="Chill",
        ),
    ]
    db.add_all(songs)
    await db.commit()
    for s in songs:
        await db.refresh(s)
    return songs


async def add_plays(
    db: AsyncSession,
    user_id: str,
    song: Song,
    count: int,
    days_ago: int = 1,
    seconds: float = 180.0,
    skipped: bool = False,
):
    now = datetime.now(timezone.utc)
    for i in range(count):
        db.add(
            ListeningHistory(
                user_id=user_id,
                song_id=song.id,
                device_id="d1",
                started_at=now - timedelta(days=days_ago, minutes=i),
                duration_listened=seconds,
                completion_percentage=min(seconds / song.duration * 100, 100.0),
                skipped=skipped,
                source="queue",
            )
        )
    await db.commit()


# --------------------------------------------------------------------------
# Empty state
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stats_with_no_history_are_zeroed_not_an_error(
    client: AsyncClient, auth_headers: dict
):
    """A brand new account opens the stats page before it has listened to anything."""
    res = await client.get("/api/me/stats", headers=auth_headers)
    assert res.status_code == 200, res.text
    data = res.json()["data"]

    assert data["total_plays"] == 0
    assert data["total_seconds_listened"] == 0.0
    assert data["distinct_songs"] == 0
    assert data["distinct_artists"] == 0
    # Division by a zero play count would otherwise 500.
    assert data["skip_rate"] == 0.0
    assert data["average_seconds_per_play"] == 0.0
    assert data["top_genres"] == []


@pytest.mark.asyncio
async def test_top_endpoints_with_no_history_are_empty(
    client: AsyncClient, auth_headers: dict
):
    for path in ("/api/me/top/tracks", "/api/me/top/artists"):
        res = await client.get(path, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert res.json()["data"]["items"] == []
        assert res.json()["data"]["total"] == 0


# --------------------------------------------------------------------------
# Top tracks
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_top_tracks_are_ranked_by_play_count(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    user_id = await register(client, auth_headers, db_session)
    songs = await seed_songs(db_session)
    await add_plays(db_session, user_id, songs[0], count=5)
    await add_plays(db_session, user_id, songs[1], count=3)
    await add_plays(db_session, user_id, songs[2], count=1)

    res = await client.get("/api/me/top/tracks", headers=auth_headers)
    assert res.status_code == 200, res.text
    items = res.json()["data"]["items"]

    assert [i["song"]["title"] for i in items] == ["Alpha", "Beta", "Gamma"]
    assert [i["play_count"] for i in items] == [5, 3, 1]
    assert [i["rank"] for i in items] == [1, 2, 3]
    assert items[0]["seconds_listened"] == pytest.approx(5 * 180.0)


@pytest.mark.asyncio
async def test_time_listened_breaks_a_play_count_tie(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Two plays heard in full beat two that were abandoned immediately."""
    user_id = await register(client, auth_headers, db_session)
    songs = await seed_songs(db_session)
    await add_plays(db_session, user_id, songs[0], count=2, seconds=5.0)
    await add_plays(db_session, user_id, songs[1], count=2, seconds=195.0)

    res = await client.get("/api/me/top/tracks", headers=auth_headers)
    items = res.json()["data"]["items"]
    assert items[0]["song"]["title"] == "Beta"


@pytest.mark.asyncio
async def test_top_tracks_respects_limit(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    user_id = await register(client, auth_headers, db_session)
    songs = await seed_songs(db_session)
    for song in songs:
        await add_plays(db_session, user_id, song, count=2)

    res = await client.get("/api/me/top/tracks?limit=2", headers=auth_headers)
    assert len(res.json()["data"]["items"]) == 2


# --------------------------------------------------------------------------
# Top artists
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_top_artists_aggregate_across_their_tracks(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    user_id = await register(client, auth_headers, db_session)
    songs = await seed_songs(db_session)
    await add_plays(db_session, user_id, songs[0], count=2)
    await add_plays(db_session, user_id, songs[1], count=2)
    await add_plays(db_session, user_id, songs[2], count=3)

    res = await client.get("/api/me/top/artists", headers=auth_headers)
    items = res.json()["data"]["items"]

    # Artist One: 4 plays over 2 tracks beats Artist Two's 3 over 1.
    assert items[0]["artist_name"] == "Artist One"
    assert items[0]["play_count"] == 4
    assert items[0]["track_count"] == 2
    assert items[1]["artist_name"] == "Artist Two"
    assert items[1]["track_count"] == 1


@pytest.mark.asyncio
async def test_artists_with_no_name_are_left_out(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    user_id = await register(client, auth_headers, db_session)
    songs = await seed_songs(db_session)
    nameless = Song(external_id="stats-x", title="Nameless", artist_name="", duration=100)
    db_session.add(nameless)
    await db_session.commit()
    await db_session.refresh(nameless)

    await add_plays(db_session, user_id, nameless, count=9)
    await add_plays(db_session, user_id, songs[0], count=1)

    res = await client.get("/api/me/top/artists", headers=auth_headers)
    names = [i["artist_name"] for i in res.json()["data"]["items"]]
    assert names == ["Artist One"]


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_totals(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    user_id = await register(client, auth_headers, db_session)
    songs = await seed_songs(db_session)
    await add_plays(db_session, user_id, songs[0], count=3, seconds=180.0)
    await add_plays(db_session, user_id, songs[2], count=1, seconds=20.0, skipped=True)

    res = await client.get("/api/me/stats", headers=auth_headers)
    data = res.json()["data"]

    assert data["total_plays"] == 4
    assert data["total_seconds_listened"] == pytest.approx(560.0)
    assert data["total_minutes_listened"] == pytest.approx(560.0 / 60, abs=0.01)
    assert data["distinct_songs"] == 2
    assert data["distinct_artists"] == 2
    assert data["skipped_plays"] == 1
    assert data["skip_rate"] == pytest.approx(0.25)
    assert data["average_seconds_per_play"] == pytest.approx(140.0)


@pytest.mark.asyncio
async def test_summary_breaks_down_taste(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    user_id = await register(client, auth_headers, db_session)
    songs = await seed_songs(db_session)
    await add_plays(db_session, user_id, songs[0], count=4)  # Pop / English / Happy
    await add_plays(db_session, user_id, songs[2], count=1)  # Rock / Hindi / Chill

    data = (await client.get("/api/me/stats", headers=auth_headers)).json()["data"]

    assert data["top_genres"][0] == {"name": "Pop", "play_count": 4}
    assert data["top_languages"][0] == {"name": "English", "play_count": 4}
    assert data["top_moods"][0] == {"name": "Happy", "play_count": 4}
    assert {g["name"] for g in data["top_genres"]} == {"Pop", "Rock"}


@pytest.mark.asyncio
async def test_songs_with_no_genre_do_not_appear_as_a_blank_bucket(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    user_id = await register(client, auth_headers, db_session)
    unlabelled = Song(external_id="stats-u", title="Unlabelled", artist_name="A", duration=100)
    db_session.add(unlabelled)
    await db_session.commit()
    await db_session.refresh(unlabelled)
    await add_plays(db_session, user_id, unlabelled, count=3)

    data = (await client.get("/api/me/stats", headers=auth_headers)).json()["data"]
    assert data["total_plays"] == 3
    assert data["top_genres"] == []
    assert data["top_moods"] == []


# --------------------------------------------------------------------------
# Range windows
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_range_window_excludes_older_plays(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    user_id = await register(client, auth_headers, db_session)
    songs = await seed_songs(db_session)
    await add_plays(db_session, user_id, songs[0], count=2, days_ago=3)
    await add_plays(db_session, user_id, songs[1], count=2, days_ago=60)
    await add_plays(db_session, user_id, songs[2], count=2, days_ago=400)

    recent = (await client.get("/api/me/stats?range=4weeks", headers=auth_headers)).json()["data"]
    assert recent["total_plays"] == 2
    assert recent["since"] is not None

    half_year = (await client.get("/api/me/stats?range=6months", headers=auth_headers)).json()["data"]
    assert half_year["total_plays"] == 4

    forever = (await client.get("/api/me/stats?range=all", headers=auth_headers)).json()["data"]
    assert forever["total_plays"] == 6
    assert forever["since"] is None


@pytest.mark.asyncio
async def test_range_applies_to_top_tracks(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    user_id = await register(client, auth_headers, db_session)
    songs = await seed_songs(db_session)
    await add_plays(db_session, user_id, songs[0], count=1, days_ago=2)
    await add_plays(db_session, user_id, songs[1], count=9, days_ago=200)

    recent = await client.get("/api/me/top/tracks?range=4weeks", headers=auth_headers)
    assert [i["song"]["title"] for i in recent.json()["data"]["items"]] == ["Alpha"]

    forever = await client.get("/api/me/top/tracks?range=all", headers=auth_headers)
    assert forever.json()["data"]["items"][0]["song"]["title"] == "Beta"


@pytest.mark.asyncio
async def test_range_is_echoed_back(client: AsyncClient, auth_headers: dict):
    for path in ("/api/me/stats", "/api/me/top/tracks", "/api/me/top/artists"):
        res = await client.get(f"{path}?range=6months", headers=auth_headers)
        assert res.json()["data"]["range"] == "6months"


@pytest.mark.asyncio
async def test_default_range_is_four_weeks(client: AsyncClient, auth_headers: dict):
    res = await client.get("/api/me/stats", headers=auth_headers)
    assert res.json()["data"]["range"] == "4weeks"


@pytest.mark.asyncio
async def test_unknown_range_is_rejected(client: AsyncClient, auth_headers: dict):
    for path in ("/api/me/stats", "/api/me/top/tracks", "/api/me/top/artists"):
        res = await client.get(f"{path}?range=lastweek", headers=auth_headers)
        assert res.status_code == 422, f"{path}: {res.status_code}"


def test_since_for_covers_every_declared_range():
    assert StatsService.since_for("all") is None
    four = StatsService.since_for("4weeks")
    six = StatsService.since_for("6months")
    assert four is not None and six is not None
    assert six < four
    # An unrecognised key must not blow up in the service layer either.
    assert StatsService.since_for("nonsense") is not None


# --------------------------------------------------------------------------
# Isolation and auth
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_one_users_plays_never_appear_in_anothers_stats(
    client: AsyncClient, auth_headers: dict, auth_headers_user2: dict, db_session: AsyncSession
):
    user_id = await register(client, auth_headers, db_session)
    other_id = await register(client, auth_headers_user2, db_session)
    songs = await seed_songs(db_session)
    await add_plays(db_session, user_id, songs[0], count=4)
    await add_plays(db_session, other_id, songs[1], count=1)

    mine = (await client.get("/api/me/stats", headers=auth_headers)).json()["data"]
    theirs = (await client.get("/api/me/stats", headers=auth_headers_user2)).json()["data"]
    assert mine["total_plays"] == 4
    assert theirs["total_plays"] == 1

    my_tracks = await client.get("/api/me/top/tracks", headers=auth_headers)
    assert [i["song"]["title"] for i in my_tracks.json()["data"]["items"]] == ["Alpha"]


@pytest.mark.asyncio
async def test_stats_require_authentication(client: AsyncClient):
    for path in ("/api/me/stats", "/api/me/top/tracks", "/api/me/top/artists"):
        res = await client.get(path)
        assert res.status_code in (401, 403), f"{path}: {res.status_code}"


@pytest.mark.asyncio
async def test_top_track_limit_is_bounded(client: AsyncClient, auth_headers: dict):
    assert (await client.get("/api/me/top/tracks?limit=0", headers=auth_headers)).status_code == 422
    assert (await client.get("/api/me/top/tracks?limit=51", headers=auth_headers)).status_code == 422


# --------------------------------------------------------------------------
# End to end through the player
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_playing_tracks_shows_up_in_stats(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """The whole feature is worthless if real playback does not feed it."""
    songs = await seed_songs(db_session)
    await client.post(
        "/api/player/play",
        json={
            "song_id": songs[0].id,
            "device_id": "d1",
            "queue": [songs[1].id],
        },
        headers=auth_headers,
    )
    # next() closes out the current track, writing the history row.
    await client.post("/api/player/next", headers=auth_headers)
    await client.post("/api/player/stop", headers=auth_headers)

    data = (await client.get("/api/me/stats", headers=auth_headers)).json()["data"]
    assert data["total_plays"] >= 1

    tracks = (await client.get("/api/me/top/tracks", headers=auth_headers)).json()["data"]
    assert tracks["total"] >= 1
    assert tracks["items"][0]["song"]["id"] in {s.id for s in songs}
