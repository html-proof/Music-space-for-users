import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Song
from app.services.library_service import LibraryService


async def seed_recommendation_catalog(db: AsyncSession):
    s1 = Song(external_id="rec-1", title="Starboy", artist_name="The Weeknd", genre="Pop", mood="Energetic", duration=230)
    s2 = Song(external_id="rec-2", title="Save Your Tears", artist_name="The Weeknd", genre="Pop", mood="Chill", duration=215)
    s3 = Song(external_id="rec-3", title="Levitating", artist_name="Dua Lipa", genre="Pop", mood="Party", duration=203)
    s4 = Song(external_id="rec-4", title="Strobe", artist_name="Deadmau5", genre="Electronic", mood="Chill", duration=620)
    db.add(s1)
    db.add(s2)
    db.add(s3)
    db.add(s4)
    await db.commit()
    await db.refresh(s1)
    await db.refresh(s2)
    await db.refresh(s3)
    await db.refresh(s4)
    return [s1, s2, s3, s4]


@pytest.mark.asyncio
async def test_recommendations_home_feed(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    songs = await seed_recommendation_catalog(db_session)
    me_res = await client.get("/api/auth/me", headers=auth_headers)
    user_id = me_res.json()["data"]["id"]

    # Like a song to seed affinity
    await LibraryService.like_song(db_session, user_id, songs[0].id)

    # Fetch home recommendations
    res = await client.get("/api/recommendations/home", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert "greeting" in data
    assert "categories" in data
    categories = data["categories"]
    assert len(categories) > 0
    cat_types = [c["category_type"] for c in categories]
    assert "made_for_you" in cat_types


@pytest.mark.asyncio
async def test_home_feed_uses_preferred_language_for_a_brand_new_user(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """A freshly onboarded user has no listening history or likes yet --
    without seeding affinities from their declared preferred_languages, the
    home feed falls back to a fully generic, unfiltered list and their
    onboarding choice is invisible until they've actually played something."""
    english_song = Song(
        external_id="rec-lang-en", title="English Track", artist_name="Artist EN",
        language="English", genre="Pop", duration=200,
    )
    malayalam_song = Song(
        external_id="rec-lang-ml", title="Malayalam Track", artist_name="Artist ML",
        language="Malayalam", genre="Pop", duration=200,
    )
    db_session.add(english_song)
    db_session.add(malayalam_song)
    await db_session.commit()

    patch_res = await client.patch(
        "/api/users/preferences", json={"preferred_languages": ["Malayalam"]}, headers=auth_headers
    )
    assert patch_res.status_code == 200

    res = await client.get("/api/recommendations/home", headers=auth_headers)
    assert res.status_code == 200
    categories = res.json()["data"]["categories"]
    made_for_you = next(c for c in categories if c["category_type"] == "made_for_you")
    languages = {item["language"] for item in made_for_you["items"]}
    assert languages == {"Malayalam"}


@pytest.mark.asyncio
async def test_home_feed_catalog_shelves_refetch_on_cache_hit(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """Trending/New Releases must never be frozen inside the cached
    personalized payload: they are refetched on every request, including
    when the personalized shelves are served from cache. This also guards
    against the shelf being duplicated by that refetch."""
    from app.services.cache_service import cache_service
    from app.services.catalog_service import catalog_service

    # catalog_service.get_trending has its own 30-minute cache in front of
    # the raw Gaana call -- bypass only that layer so this test can observe
    # the raw call happening again, without disturbing the home-feed cache
    # this test is actually exercising.
    real_get_json = cache_service.get_json

    async def get_json_bypassing_trending_cache(key: str):
        if key.startswith("catalog:trending:") or key.startswith("catalog:newreleases:"):
            return None
        return await real_get_json(key)

    monkeypatch.setattr(cache_service, "get_json", get_json_bypassing_trending_cache)

    calls = {"trending": 0}

    async def fake_get_trending(language, limit):
        calls["trending"] += 1
        return [{
            "seokey": f"cache-check-{calls['trending']}",
            "title": "Cache Check Track",
            "artists": "Cache Check Artist",
            "album": "Single",
            "duration": "100",
            "images": {"urls": {}},
            "stream_urls": {"urls": {}},
            "language": language,
            "genres": "Pop",
            "is_explicit": False,
        }]

    async def empty_new_releases(language, limit):
        return {"tracks": []}

    monkeypatch.setattr(catalog_service.gaana, "get_trending", fake_get_trending)
    monkeypatch.setattr(catalog_service.gaana, "get_new_releases", empty_new_releases)

    await seed_recommendation_catalog(db_session)

    first = await client.get("/api/recommendations/home", headers=auth_headers)
    assert first.status_code == 200
    first_categories = first.json()["data"]["categories"]
    trending_first = [c for c in first_categories if c["category_type"] == "trending"]
    assert len(trending_first) == 1

    second = await client.get("/api/recommendations/home", headers=auth_headers)
    assert second.status_code == 200
    second_categories = second.json()["data"]["categories"]
    trending_second = [c for c in second_categories if c["category_type"] == "trending"]
    # Exactly one -- not zero (it must still be there on a cache hit) and not
    # two (the idempotent-append guard must not have failed).
    assert len(trending_second) == 1
    # The mock was actually called again, proving this shelf was not served
    # from the personalized-shelves cache.
    assert calls["trending"] >= 2


@pytest.mark.asyncio
async def test_similar_songs_and_moods(client: AsyncClient, db_session: AsyncSession):
    songs = await seed_recommendation_catalog(db_session)

    # Similar songs
    sim_res = await client.get(f"/api/recommendations/similar-song/{songs[0].id}")
    assert sim_res.status_code == 200
    similar = sim_res.json()["data"]
    assert len(similar) > 0

    # Mood mix
    mood_res = await client.get("/api/recommendations/mood/Chill")
    assert mood_res.status_code == 200
    chill_songs = mood_res.json()["data"]
    assert len(chill_songs) > 0
