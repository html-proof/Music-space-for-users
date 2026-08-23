import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Artist, Song


async def seed_artists(db: AsyncSession):
    a1 = Artist(external_id="art-ob-1", name="Daft Punk", song_count=5)
    a2 = Artist(external_id="art-ob-2", name="Justice", song_count=3)
    db.add(a1)
    db.add(a2)
    await db.commit()
    await db.refresh(a1)
    await db.refresh(a2)
    return a1, a2


async def seed_artists_with_language_songs(db: AsyncSession):
    """Two artists, each with a song in a different language."""
    a_en = Artist(external_id="art-lang-en", name="English Artist", song_count=1)
    a_ml = Artist(external_id="art-lang-ml", name="Malayalam Artist", song_count=1)
    db.add(a_en)
    db.add(a_ml)
    await db.commit()
    await db.refresh(a_en)
    await db.refresh(a_ml)

    db.add(Song(
        external_id="song-lang-en", title="English Song", artist_id=a_en.id,
        artist_name=a_en.name, language="English", duration=180,
    ))
    db.add(Song(
        external_id="song-lang-ml", title="Malayalam Song", artist_id=a_ml.id,
        artist_name=a_ml.name, language="Malayalam", duration=180,
    ))
    await db.commit()
    return a_en, a_ml


async def seed_languages(db: AsyncSession):
    """Languages are derived from the songs catalog, not a seeded table --
    this stands in for songs having actually been ingested in those
    languages."""
    db.add_all([
        Song(external_id="lang-seed-en", title="Seed Song EN", language="English", duration=180),
        Song(external_id="lang-seed-ml", title="Seed Song ML", language="Malayalam", duration=180),
        Song(external_id="lang-seed-ta", title="Seed Song TA", language="Tamil", duration=180),
    ])
    await db.commit()


@pytest.mark.asyncio
async def test_status_unauthorized(client: AsyncClient):
    res = await client.get("/api/onboarding/status")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_status_defaults_to_incomplete(client: AsyncClient, auth_headers: dict):
    res = await client.get("/api/onboarding/status", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["completed"] is False
    assert data["completed_at"] is None
    # sync_user starts a new user with no preferred languages at all -- it is
    # never allowed to guess one on the user's behalf.
    assert data["preferred_languages"] == []
    assert data["favorite_artists"] == []


@pytest.mark.asyncio
async def test_get_languages_catalog(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    await seed_languages(db_session)
    res = await client.get("/api/onboarding/languages", headers=auth_headers)
    assert res.status_code == 200
    names = {lang["name"] for lang in res.json()["data"]}
    assert names == {"English", "Malayalam", "Tamil"}


@pytest.mark.asyncio
async def test_set_languages(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    await seed_languages(db_session)
    res = await client.post(
        "/api/onboarding/languages", json={"languages": ["Malayalam", "Tamil"]}, headers=auth_headers
    )
    assert res.status_code == 200
    assert res.json()["data"]["preferred_languages"] == ["Malayalam", "Tamil"]

    prefs_res = await client.get("/api/users/preferences", headers=auth_headers)
    assert prefs_res.json()["data"]["preferred_languages"] == ["Malayalam", "Tamil"]


@pytest.mark.asyncio
async def test_set_languages_requires_nonempty_list(client: AsyncClient, auth_headers: dict):
    res = await client.post("/api/onboarding/languages", json={"languages": []}, headers=auth_headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_set_languages_rejects_unknown_language(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    await seed_languages(db_session)
    res = await client.post(
        "/api/onboarding/languages", json={"languages": ["Klingon"]}, headers=auth_headers
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_LANGUAGES"


@pytest.mark.asyncio
async def test_set_languages_filters_unknown_and_keeps_valid(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    await seed_languages(db_session)
    res = await client.post(
        "/api/onboarding/languages", json={"languages": ["malayalam", "Klingon"]}, headers=auth_headers
    )
    assert res.status_code == 200
    # Case-insensitive match resolves to the catalog's canonical casing.
    assert res.json()["data"]["preferred_languages"] == ["Malayalam"]


@pytest.mark.asyncio
async def test_get_suggested_artists(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    # limit == seeded count so the thin-catalog fallback (a real network call
    # to catalog_service.gaana.get_trending) never triggers in this test.
    a1, a2 = await seed_artists(db_session)
    res = await client.get("/api/onboarding/artists/suggestions?limit=2", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    names = [a["name"] for a in data]
    # Ranked by song_count desc.
    assert names[:2] == ["Daft Punk", "Justice"]


@pytest.mark.asyncio
async def test_get_suggested_artists_biased_by_preferred_language(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Onboarding is language-then-artists: by the time this screen loads,
    the user's language choice is already saved and should drive which
    artists show up first, not a fixed default."""
    await seed_languages(db_session)
    a_en, a_ml = await seed_artists_with_language_songs(db_session)

    await client.post(
        "/api/onboarding/languages", json={"languages": ["Malayalam"]}, headers=auth_headers
    )

    res = await client.get("/api/onboarding/artists/suggestions?limit=1", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert len(data) == 1
    assert data[0]["name"] == "Malayalam Artist"


def _raw_trending_track(language: str) -> dict:
    return {
        "seokey": f"fallback-song-{language}",
        "track_id": f"fallback-song-{language}",
        "title": "Fallback Track",
        "artists": f"Fallback Artist {language}",
        "artist_seokeys": f"fallback-artist-{language.lower()}",
        "album": "Single",
        "duration": "100",
        "images": {"urls": {}},
        "stream_urls": {"urls": {}},
        "language": language,
        "genres": "Pop",
        "is_explicit": False,
    }


@pytest.mark.asyncio
async def test_get_suggested_artists_falls_back_when_catalog_thin(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """Only the raw Gaana network call is mocked (not catalog_service.get_trending
    as a whole) -- get_suggested_artists deliberately calls the raw client
    itself and does its own upsert, so that a timeout only ever cancels the
    network leg, never a live DB commit. See onboarding_service.py."""
    from app.services.catalog_service import catalog_service

    async def fake_get_trending(language, limit):
        return [_raw_trending_track(language)]

    monkeypatch.setattr(catalog_service.gaana, "get_trending", fake_get_trending)

    res = await client.get("/api/onboarding/artists/suggestions?limit=20", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    names = {a["name"] for a in data}
    assert "Fallback Artist English" in names
    assert "Fallback Artist Hindi" in names
    # Regression: the response must carry Gaana's real seokey, not just the
    # numeric id, or a later artist-detail fetch for a selected artist has
    # nothing valid to query Gaana with.
    by_name = {a["name"]: a for a in data}
    assert by_name["Fallback Artist English"]["seokey"] == "fallback-artist-english"


@pytest.mark.asyncio
async def test_get_suggested_artists_skips_language_on_upstream_failure(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """An unreachable/erroring upstream must not fail the whole request --
    onboarding should still respond (possibly with an empty list) rather
    than 500 or hang."""
    from app.services.catalog_service import catalog_service

    async def failing_get_trending(language, limit):
        raise RuntimeError("upstream unreachable")

    monkeypatch.setattr(catalog_service.gaana, "get_trending", failing_get_trending)

    res = await client.get("/api/onboarding/artists/suggestions?limit=20", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["data"] == []


@pytest.mark.asyncio
async def test_set_artists_updates_preferences_and_follows(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    a1, a2 = await seed_artists(db_session)

    res = await client.post(
        "/api/onboarding/artists",
        json={"artists": [{"id": a1.id}, {"id": a2.id}]},
        headers=auth_headers,
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert set(data["favorite_artists"]) == {"Daft Punk", "Justice"}

    followed_res = await client.get("/api/library/artists", headers=auth_headers)
    followed_names = {a["name"] for a in followed_res.json()["data"]}
    assert followed_names == {"Daft Punk", "Justice"}


@pytest.mark.asyncio
async def test_set_artists_persists_seokey_for_unpersisted_selection(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Selecting a Gaana-only artist (never in our DB) must persist its real
    seokey, not just its numeric id -- without this, GET
    /api/catalog/artists/info later has nothing valid to query Gaana with
    (this was a real bug: artists selected off the unpersisted suggestions
    list saved with seokey=None, and opening them from Library timed out)."""
    res = await client.post(
        "/api/onboarding/artists",
        json={"artists": [{
            "id": "774738",
            "name": "Arijit Singh",
            "seokey": "arijit-singh",
            "image_url": "https://example.com/art.jpg",
        }]},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["data"]["favorite_artists"] == ["Arijit Singh"]

    followed_res = await client.get("/api/library/artists", headers=auth_headers)
    followed = followed_res.json()["data"]
    assert len(followed) == 1
    assert followed[0]["seokey"] == "arijit-singh"


@pytest.mark.asyncio
async def test_set_artists_skips_unknown_ids(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    a1, _ = await seed_artists(db_session)

    res = await client.post(
        "/api/onboarding/artists",
        json={"artists": [{"id": a1.id}, {"id": "not-a-real-id"}]},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["data"]["favorite_artists"] == ["Daft Punk"]


@pytest.mark.asyncio
async def test_complete_flow(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    a1, _ = await seed_artists(db_session)
    await seed_languages(db_session)

    await client.post("/api/onboarding/languages", json={"languages": ["English"]}, headers=auth_headers)
    await client.post(
        "/api/onboarding/artists", json={"artists": [{"id": a1.id}]}, headers=auth_headers
    )

    complete_res = await client.post("/api/onboarding/complete", headers=auth_headers)
    assert complete_res.status_code == 200
    data = complete_res.json()["data"]
    assert data["completed"] is True
    assert data["completed_at"] is not None

    status_res = await client.get("/api/onboarding/status", headers=auth_headers)
    assert status_res.json()["data"]["completed"] is True
