import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Artist


async def seed_artists(db: AsyncSession):
    a1 = Artist(external_id="art-ob-1", name="Daft Punk", song_count=5)
    a2 = Artist(external_id="art-ob-2", name="Justice", song_count=3)
    db.add(a1)
    db.add(a2)
    await db.commit()
    await db.refresh(a1)
    await db.refresh(a2)
    return a1, a2


#: Languages the stubbed Gaana serves, shaped like the chart records
#: `catalog_service.get_languages` discovers them from. The multi-language entry
#: is real: Gaana stamps some charts with a comma-joined list.
CHART_LANGUAGES = ("Malayalam", "Tamil", "English", "Tamil,Kannada")


def stub_languages(monkeypatch, languages=CHART_LANGUAGES):
    """Make Gaana chart listing report `languages`.

    Selectable languages used to be `SELECT DISTINCT language FROM songs`, so
    every test touching the language screen first had to write songs; then they
    were a hardcoded config list, so no stub was needed at all. They are now
    discovered from Gaana own top-charts listing, which is what this stands in
    for -- nothing about the local database affects the result.
    """
    from app.services.catalog_service import catalog_service

    async def get_charts(limit, *args, **kwargs):
        return [
            {"seokey": f"chart-{i}", "title": f"Chart {i}", "language": language}
            for i, language in enumerate(languages)
        ]

    monkeypatch.setattr(catalog_service.gaana, "get_charts", get_charts)


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
async def test_get_languages_comes_from_gaana_not_the_songs_table(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """The language screen offers what Gaana serves, on a completely empty
    database.

    Two regressions in one: the list used to be derived from ingested songs (so
    a fresh deployment offered nothing and onboarding was unusable until some
    other request warmed the catalog up), and then from a hardcoded config list
    (so the names were ours, and nothing kept them true). A comma-joined chart
    is split, because that is a listing of languages rather than a language.
    """
    stub_languages(monkeypatch)

    res = await client.get("/api/onboarding/languages", headers=auth_headers)
    assert res.status_code == 200
    names = {lang["name"] for lang in res.json()["data"]}
    assert names == {"Malayalam", "Tamil", "English", "Kannada"}


@pytest.mark.asyncio
async def test_get_languages_is_empty_when_gaana_is_unreachable(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """No default list to fall back on.

    Gaana is unreachable (the suite default), so the screen gets nothing and
    shows a retry. Substituting a canned set of languages here is what would
    make the app look populated when it is not -- and would let a user pick a
    language this backend cannot actually serve content for.
    """
    res = await client.get("/api/onboarding/languages", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["data"] == []


@pytest.mark.asyncio
async def test_set_languages(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    stub_languages(monkeypatch)
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
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """Rejected because Gaana does not serve it, not because we have no rows."""
    stub_languages(monkeypatch)
    res = await client.post(
        "/api/onboarding/languages", json={"languages": ["Klingon"]}, headers=auth_headers
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_LANGUAGES"


@pytest.mark.asyncio
async def test_set_languages_filters_unknown_and_keeps_valid(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    stub_languages(monkeypatch)
    res = await client.post(
        "/api/onboarding/languages", json={"languages": ["malayalam", "Klingon"]}, headers=auth_headers
    )
    assert res.status_code == 200
    # Case-insensitive match resolves to the catalog's canonical casing.
    assert res.json()["data"]["preferred_languages"] == ["Malayalam"]


@pytest.mark.asyncio
async def test_get_suggested_artists_come_from_gaana_not_the_artists_table(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """Suggestions are Gaana artists, even when the local `artists` table has
    rows that would previously have won.

    The old implementation led with `SELECT ... FROM artists ORDER BY
    song_count`, so the grid showed whatever earlier requests had written --
    and once that table filled up, Gaana was never consulted at all.
    """
    from app.services.catalog_service import catalog_service

    await seed_artists(db_session)  # Daft Punk / Justice: must NOT be suggested

    async def fake_get_trending(language, limit):
        return [_raw_trending_track(language)]

    async def fake_search_artists(name, limit):
        return [{"name": name, "images": {"urls": {}}}]

    monkeypatch.setattr(catalog_service.gaana, "get_trending", fake_get_trending)
    monkeypatch.setattr(catalog_service.gaana, "search_artists", fake_search_artists)
    # No language chosen yet, so the screen asks Gaana which languages it has
    # most of rather than assuming a pair.
    stub_languages(monkeypatch, ("Hindi", "English"))

    res = await client.get("/api/onboarding/artists/suggestions?limit=20", headers=auth_headers)
    assert res.status_code == 200
    names = {a["name"] for a in res.json()["data"]}
    assert names == {"Fallback Artist Hindi", "Fallback Artist English"}
    assert "Daft Punk" not in names and "Justice" not in names


@pytest.mark.asyncio
async def test_get_suggested_artists_queries_gaana_for_the_chosen_language(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """Onboarding is language-then-artists: the language the user just saved
    must be the language this screen asks Gaana about.

    The preference is read from Postgres (user data); the artists come back
    from Gaana for exactly that language.
    """
    from app.services.catalog_service import catalog_service

    asked = []

    async def fake_get_trending(language, limit):
        asked.append(language)
        return [_raw_trending_track(language)]

    async def fake_search_artists(name, limit):
        return [{"name": name, "images": {"urls": {}}}]

    monkeypatch.setattr(catalog_service.gaana, "get_trending", fake_get_trending)
    monkeypatch.setattr(catalog_service.gaana, "search_artists", fake_search_artists)
    stub_languages(monkeypatch)

    await client.post(
        "/api/onboarding/languages", json={"languages": ["Malayalam"]}, headers=auth_headers
    )

    res = await client.get("/api/onboarding/artists/suggestions?limit=20", headers=auth_headers)
    assert res.status_code == 200
    assert asked == ["Malayalam"]
    assert [a["name"] for a in res.json()["data"]] == ["Fallback Artist Malayalam"]


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
async def test_get_suggested_artists_carries_seokey_and_backfilled_image(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """Only the raw Gaana network call is mocked (not catalog_service.get_trending
    as a whole) -- get_suggested_artists deliberately calls the raw client
    itself and persists nothing, so that a timeout only ever cancels the
    network leg. See onboarding_service.py."""
    from app.services.catalog_service import catalog_service

    async def fake_get_trending(language, limit):
        return [_raw_trending_track(language)]

    async def fake_search_artists(name, limit):
        return [{"name": name, "images": {"urls": {"large_artwork": f"https://img.example/{name}.jpg"}}}]

    monkeypatch.setattr(catalog_service.gaana, "get_trending", fake_get_trending)
    monkeypatch.setattr(catalog_service.gaana, "search_artists", fake_search_artists)
    stub_languages(monkeypatch, ("Hindi", "English"))

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
    # The raw trending payload carries no artist_image -- this must be
    # backfilled from a real Gaana artist search, not left as a placeholder.
    assert by_name["Fallback Artist English"]["image_url"] == "https://img.example/Fallback Artist English.jpg"


@pytest.mark.asyncio
async def test_get_suggested_artists_image_backfill_is_bounded_and_fault_tolerant(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """A failing/slow image lookup for one artist must not break the whole
    suggestions response -- it just leaves that one artist without an image."""
    from app.services.catalog_service import catalog_service

    async def fake_get_trending(language, limit):
        return [_raw_trending_track(language)]

    async def failing_search_artists(name, limit):
        raise RuntimeError("gaana artist search unreachable")

    monkeypatch.setattr(catalog_service.gaana, "get_trending", fake_get_trending)
    monkeypatch.setattr(catalog_service.gaana, "search_artists", failing_search_artists)
    stub_languages(monkeypatch, ("Hindi", "English"))

    res = await client.get("/api/onboarding/artists/suggestions?limit=20", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert len(data) >= 2
    assert all(a["image_url"] is None for a in data)


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
async def test_complete_flow(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    a1, _ = await seed_artists(db_session)
    stub_languages(monkeypatch)

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
