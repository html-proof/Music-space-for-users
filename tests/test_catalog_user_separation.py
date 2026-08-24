"""The separation the whole architecture rests on.

    Gaana    = the catalog: languages, artists, albums, songs, recommendations
    Postgres = this user: preferences, likes, history, playlists

Two consequences of that split are easy to regress silently, so they are pinned
here rather than left implied:

* one catalog record is shared by every user who touches it -- ten thousand
  listeners of the same song must not produce ten thousand song rows;
* preferences are keyed to the authenticated user, so wiping the device (or
  reinstalling) restores them from the same Firebase UID, and the content behind
  them is re-fetched live rather than reset to anything default.

Gaana is unreachable by default in this suite (see `conftest.offline_gaana`),
which is itself part of the point: nothing below may be satisfied by rows that
happen to be sitting in the database.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.song import Artist, LikedSong, Song
from app.models.user import UserPreferences
from app.services.catalog_service import catalog_service

RAW_TRACK = {
    "seokey": "shared-track",
    "track_id": "shared-track",
    "title": "Shared Track",
    "artists": "Shared Artist",
    "artist_seokeys": "shared-artist",
    "artist_ids": "4242",
    "album": "Shared Album",
    "album_seokey": "shared-album",
    "album_id": "8484",
    "duration": "200",
    "language": "Malayalam",
    "genres": "Pop",
    "is_explicit": False,
    "images": {"urls": {"large_artwork": "https://img/large.jpg"}},
    "stream_urls": {"urls": {"high_quality": "https://stream/hq.mp4"}},
}


def stub_gaana(monkeypatch, tracks=(RAW_TRACK,), languages=("Malayalam", "Tamil")):
    async def search_songs(query, limit=10, *args, **kwargs):
        return list(tracks)[:limit] or {"error": "no results found"}

    async def get_trending(language="English", limit=10, *args, **kwargs):
        return [t for t in tracks if t["language"] == language][:limit]

    async def get_charts(limit, *args, **kwargs):
        return [
            {"seokey": f"chart-{i}", "title": f"Chart {i}", "language": language}
            for i, language in enumerate(languages)
        ]

    monkeypatch.setattr(catalog_service.gaana, "search_songs", search_songs)
    monkeypatch.setattr(catalog_service.gaana, "get_trending", get_trending)
    monkeypatch.setattr(catalog_service.gaana, "get_charts", get_charts)


@pytest.mark.asyncio
async def test_many_users_share_one_catalog_record(
    client: AsyncClient, auth_headers: dict, auth_headers_user2: dict,
    db_session: AsyncSession, monkeypatch,
):
    """Two users reaching the same Gaana track produce one row, not one each.

    Catalog rows are keyed by Gaana own `seokey` (`songs.external_id`, unique),
    so the upsert resolves to the existing row however many users arrive at it.
    Their activity references that shared row -- which is what keeps the table
    proportional to the catalog rather than to the user base.
    """
    stub_gaana(monkeypatch)

    for headers in (auth_headers, auth_headers_user2):
        res = await client.get("/api/search?query=Shared&type=track", headers=headers)
        assert res.status_code == 200, res.text
        assert [s["title"] for s in res.json()["data"]["songs"]] == ["Shared Track"]

    for model, external_id in ((Song, "shared-track"), (Artist, "4242")):
        count = await db_session.scalar(
            select(func.count()).select_from(model).where(model.external_id == external_id)
        )
        assert count == 1, f"{model.__tablename__} duplicated per user"

    # ...and both users like that one row, rather than each liking a copy.
    song_id = (
        await db_session.execute(select(Song.id).where(Song.external_id == "shared-track"))
    ).scalar_one()

    for headers in (auth_headers, auth_headers_user2):
        like = await client.post(f"/api/songs/{song_id}/like", headers=headers)
        assert like.status_code in (200, 201), like.text

    likes = (
        await db_session.execute(select(LikedSong).where(LikedSong.song_id == song_id))
    ).scalars().all()
    assert len(likes) == 2
    assert len({like.user_id for like in likes}) == 2


@pytest.mark.asyncio
async def test_preferences_survive_a_reinstall_and_content_is_refetched(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch,
):
    """Reinstall restores the user own choices, never a default set.

    The bearer token carries the Firebase UID, so a fresh install with no local
    state resolves to the same user row and the same preferences. Nothing about
    the feed is restored from the device -- the songs behind those preferences
    are fetched from Gaana again, which is why the shelf below is populated only
    because Gaana is reachable in this test.
    """
    stub_gaana(monkeypatch)

    set_res = await client.post(
        "/api/onboarding/languages", json={"languages": ["Malayalam"]}, headers=auth_headers
    )
    assert set_res.status_code == 200, set_res.text
    await client.post("/api/onboarding/complete", headers=auth_headers)

    me = await client.get("/api/auth/me", headers=auth_headers)
    user_id = me.json()["data"]["id"]

    # The reinstall: no client state at all, just the same Firebase identity.
    # Server-side caches are dropped too, so nothing is answered from a snapshot
    # taken before the "reinstall".
    from app.services.cache_service import cache_service
    cache_service._memory_cache.clear()

    status = await client.get("/api/onboarding/status", headers=auth_headers)
    assert status.status_code == 200, status.text
    assert status.json()["data"]["completed"] is True
    assert status.json()["data"]["preferred_languages"] == ["Malayalam"]

    # The preference is stored against the user uuid, not the device.
    stored = (
        await db_session.execute(
            select(UserPreferences.preferred_languages).where(UserPreferences.user_id == user_id)
        )
    ).scalar_one()
    assert stored == ["Malayalam"]

    # And the content behind it is fetched fresh from Gaana for that language.
    feed = await client.get("/api/recommendations/home", headers=auth_headers)
    assert feed.status_code == 200, feed.text
    titles = {
        item["title"]
        for category in feed.json()["data"]["categories"]
        for item in category["items"]
    }
    assert titles == {"Shared Track"}


@pytest.mark.asyncio
async def test_a_fresh_install_starts_genuinely_empty(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """A brand-new user with Gaana unreachable gets nothing preset.

    No default language, no default artists, no default shelves. Every one of
    those would be an artificial record existing only to make the app look
    populated, and would misrepresent an outage as a working feed.
    """
    languages = await client.get("/api/onboarding/languages", headers=auth_headers)
    assert languages.json()["data"] == []

    artists = await client.get("/api/onboarding/artists/suggestions", headers=auth_headers)
    assert artists.json()["data"] == []

    status = await client.get("/api/onboarding/status", headers=auth_headers)
    assert status.json()["data"]["preferred_languages"] == []
    assert status.json()["data"]["favorite_artists"] == []

    feed = await client.get("/api/recommendations/home", headers=auth_headers)
    assert feed.status_code == 200, feed.text
    data = feed.json()["data"]
    assert data["top_mix"] == []
    assert all(category["items"] == [] for category in data["categories"])

    # Nothing was written to the catalog tables on the way through.
    for model in (Song, Artist):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0
