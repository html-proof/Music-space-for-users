import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Song
from app.services.library_service import LibraryService


def _raw_track(song: Song) -> dict:
    """The Gaana payload a seeded row corresponds to.

    Same seokey, so `upsert_gaana_song` resolves to the existing row and the
    ids a test asserts on stay stable.
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


def serve_upstream(monkeypatch, songs):
    """Make Gaana serve `songs`.

    Shelves are built by *querying Gaana* with the terms the user preferences
    imply, so a test that wants a populated feed has to put the tracks upstream
    -- seeding the database no longer has any effect on what is recommended,
    which is the entire point of the change.

    Matching is deliberately loose (any query token against any field) except
    for language, which is filtered exactly: several tests turn on the feed
    respecting the language the user chose.
    """
    from app.services.catalog_service import catalog_service

    tracks = [_raw_track(song) for song in songs]

    def _for_language(language):
        return [t for t in tracks if not language or t["language"] == language]

    def _matching(query):
        tokens = [t for t in (query or "").lower().split() if t]
        matched = []
        for track in tracks:
            haystack = " ".join(
                str(track.get(f) or "")
                for f in ("title", "artists", "genres", "mood", "language")
            ).lower()
            if any(token in haystack for token in tokens):
                matched.append(track)
        return matched

    async def search_songs(query, limit=10, *args, **kwargs):
        return _matching(query)[:limit] or {"error": "no results found"}

    async def get_trending(language="English", limit=10, *args, **kwargs):
        return _for_language(language)[:limit]

    async def get_new_releases(language="English", limit=10, *args, **kwargs):
        return {"tracks": _for_language(language)[:limit]}

    async def get_track_info(seokeys, *args, **kwargs):
        wanted = set(seokeys or [])
        return [t for t in tracks if t["seokey"] in wanted] or {"error": "no results found"}

    monkeypatch.setattr(catalog_service.gaana, "search_songs", search_songs)
    monkeypatch.setattr(catalog_service.gaana, "get_trending", get_trending)
    monkeypatch.setattr(catalog_service.gaana, "get_new_releases", get_new_releases)
    monkeypatch.setattr(catalog_service.gaana, "get_track_info", get_track_info)


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
async def test_home_feed_refresh_bypasses_the_personalized_cache(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """Pull-to-refresh must force a real recompute, not just re-serve
    whatever the server already cached -- otherwise the gesture is a no-op
    and the feed looks static even when the user asks it to update."""
    from app.services.cache_service import cache_service
    from app.utils.cache_keys import home_recommendations_key

    songs = await seed_recommendation_catalog(db_session)
    serve_upstream(monkeypatch, songs)
    me_res = await client.get("/api/auth/me", headers=auth_headers)
    user_id = me_res.json()["data"]["id"]
    await LibraryService.like_song(db_session, user_id, songs[0].id)

    # A recognizable stand-in for "whatever got cached earlier" -- planted
    # directly so this test doesn't depend on the real ranking producing a
    # specific order.
    await cache_service.set_json(
        home_recommendations_key(user_id),
        {
            "greeting": "stale",
            "top_mix": [],
            "categories": [{
                "id": "made_for_you", "title": "Made For You", "description": "",
                "category_type": "made_for_you", "items": [],
            }],
        },
        ttl_seconds=3600,
    )

    normal = await client.get("/api/recommendations/home", headers=auth_headers)
    assert normal.status_code == 200
    made_for_you = next(c for c in normal.json()["data"]["categories"] if c["category_type"] == "made_for_you")
    assert made_for_you["items"] == []  # served straight from the planted cache

    refreshed = await client.get("/api/recommendations/home?refresh=true", headers=auth_headers)
    assert refreshed.status_code == 200
    made_for_you_refreshed = next(
        c for c in refreshed.json()["data"]["categories"] if c["category_type"] == "made_for_you"
    )
    assert made_for_you_refreshed["items"] != []  # recomputed, not the stale cache


@pytest.mark.asyncio
async def test_home_feed_uses_preferred_language_for_a_brand_new_user(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """A freshly onboarded user has no listening history or likes yet --
    without seeding affinities from their declared preferred_languages, the
    home feed falls back to a fully generic, unfiltered list and their
    onboarding choice is invisible until they have actually played something.

    The declared language is read from Postgres and used as the *Gaana query*,
    so what this checks end to end is preference (our data) -> Gaana (their
    catalog) -> shelf."""
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
    await db_session.refresh(english_song)
    await db_session.refresh(malayalam_song)
    serve_upstream(monkeypatch, [english_song, malayalam_song])

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
async def test_home_feed_is_empty_when_gaana_is_down_even_with_a_full_database(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """The headline rule, end to end: Postgres is never a fallback catalog.

    A database stuffed with popular songs in the user own preferred language --
    exactly the rows every old fallback path selected -- plus an unreachable
    Gaana. Every shelf must come back empty. Serving those rows is what made the
    feed look frozen: it recommended whatever had been ingested once, forever,
    and the client had no way to tell that from a real recommendation.
    """
    for i in range(20):
        db_session.add(Song(
            external_id=f"stale-home-{i}",
            title=f"Stale Home {i}",
            artist_name="Stale Artist",
            genre="Pop",
            mood="Chill",
            language="English",
            duration=200,
            play_count=1000 + i,
        ))
    await db_session.commit()

    res = await client.get("/api/recommendations/home", headers=auth_headers)
    assert res.status_code == 200, res.text
    data = res.json()["data"]

    assert data["top_mix"] == []
    for category in data["categories"]:
        assert category["items"] == [], f"{category['category_type']} served local rows"


@pytest.mark.asyncio
async def test_home_feed_serves_exactly_what_gaana_returned(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """Preferences (Postgres) -> Gaana -> shelves, with nothing else mixed in.

    A stale row that matches the user preferences perfectly is seeded alongside,
    and must not appear: the feed is the Gaana response, not a union of it with
    whatever the database already held.
    """
    db_session.add(Song(
        external_id="stale-mixed-in", title="Stale Mixed In", artist_name="Stale Artist",
        genre="Pop", language="English", duration=200, play_count=9999,
    ))
    await db_session.commit()

    live = Song(
        external_id="live-gaana-track", title="Live Gaana Track", artist_name="Live Artist",
        genre="Pop", language="English", duration=210,
    )
    db_session.add(live)
    await db_session.commit()
    await db_session.refresh(live)
    serve_upstream(monkeypatch, [live])

    res = await client.get("/api/recommendations/home", headers=auth_headers)
    assert res.status_code == 200, res.text
    data = res.json()["data"]

    served = {
        item["title"]
        for category in data["categories"]
        for item in category["items"]
    } | {item["title"] for item in data["top_mix"]}

    assert served, "the feed should have been populated from Gaana"
    assert served == {"Live Gaana Track"}
    assert "Stale Mixed In" not in served


@pytest.mark.asyncio
async def test_favorite_artists_retrieve_candidates_before_any_history(
    db_session: AsyncSession, monkeypatch
):
    """A freshly onboarded user has no artist_affinity yet (that only builds
    from actual plays/likes) -- their declared favorite_artists must still
    drive retrieval, the same way favorite_genres and preferred_languages do.
    Without this, picking artists during onboarding had no effect until the
    user had actually played something by them.

    Retrieval now means *asking Gaana for that artist*, so what this asserts is
    that the stored preference becomes the upstream query, and that the tracks
    Gaana returns are what land in the candidate set."""
    from datetime import datetime, timezone
    from app.ml import candidates as ml_candidates
    from app.ml.features import UserState, song_vector
    from app.services.catalog_service import catalog_service

    queried = []

    async def fake_search_songs(query, limit=10, *args, **kwargs):
        queried.append(query)
        return [{
            "seokey": "gaana-chosen-artist-track",
            "track_id": "gaana-chosen-artist-track",
            "title": "Chosen Artist Song",
            "artists": "Chosen Artist",
            "album": "Single",
            "duration": "200",
            "language": "English",
            "genres": "Pop",
            "is_explicit": False,
            "images": {"urls": {}},
            "stream_urls": {"urls": {}},
        }]

    monkeypatch.setattr(catalog_service.gaana, "search_songs", fake_search_songs)

    seed = Song(
        external_id="rec-fav-artist-seed", title="Seed", artist_name="Chosen Artist",
        genre="Pop", duration=200,
    )
    db_session.add(seed)
    await db_session.commit()

    state = UserState(
        user_id="anon-fixture",
        as_of=datetime.now(timezone.utc),
        taste_vector=song_vector(seed),
        favorite_artists={"chosen artist"},
    )

    cand = ml_candidates.CandidateSet()
    await ml_candidates._from_artist_genre(
        db_session, state, cand, cap=10, exclude=set(), budget=ml_candidates._Budget(),
    )

    assert "chosen artist" in queried, "the declared favourite must be the Gaana query"
    titles = {song.title for song in cand.songs.values()}
    assert titles == {"Chosen Artist Song"}


@pytest.mark.asyncio
async def test_candidate_retrieval_ignores_the_local_songs_table(db_session: AsyncSession):
    """With Gaana unreachable, retrieval returns nothing -- it must not fall
    back to scanning `songs`.

    A catalogue that would previously have satisfied every source is seeded
    first, so the assertion is specifically that none of it is retrieved. This
    is the defect the whole change is about: the home feed could only ever
    recommend rows some earlier request had happened to ingest.
    """
    from datetime import datetime, timezone
    from app.ml import candidates as ml_candidates
    from app.ml.features import UserState, song_vector

    stale = [
        Song(
            external_id=f"stale-cand-{i}", title=f"Stale {i}", artist_name="Stale Artist",
            genre="Pop", language="English", duration=200, play_count=500 + i,
        )
        for i in range(10)
    ]
    for song in stale:
        db_session.add(song)
    await db_session.commit()

    state = UserState(
        user_id="anon-fixture",
        as_of=datetime.now(timezone.utc),
        taste_vector=song_vector(stale[0]),
        favorite_artists={"stale artist"},
        preferred_languages={"English"},
        favorite_genres={"Pop"},
    )

    # Gaana is unreachable (suite default), so every source comes back empty.
    pool = await ml_candidates.generate(db_session, state, limit=50)
    assert len(pool) == 0


@pytest.mark.asyncio
async def test_home_feed_trending_falls_back_to_english_when_preferred_language_has_no_coverage(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """A user's preferred language may have no Gaana trending coverage (or
    that call may fail/time out) -- the Trending shelf must still show
    something rather than leaving the home screen with nothing in it,
    the same way onboarding's artist suggestions already fall back across
    languages."""
    from app.services.catalog_service import catalog_service

    async def fake_get_trending(language, limit):
        if language != "English":
            return []  # no coverage for the user's actual preference
        return [{
            "seokey": "fallback-trending-track",
            "title": "Fallback Trending Track",
            "artists": "Fallback Trending Artist",
            "album": "Single",
            "duration": "100",
            "images": {"urls": {}},
            "stream_urls": {"urls": {}},
            "language": "English",
            "genres": "Pop",
            "is_explicit": False,
        }]

    async def empty_new_releases(language, limit):
        return {"tracks": []}

    monkeypatch.setattr(catalog_service.gaana, "get_trending", fake_get_trending)
    monkeypatch.setattr(catalog_service.gaana, "get_new_releases", empty_new_releases)

    patch_res = await client.patch(
        "/api/users/preferences", json={"preferred_languages": ["Malayalam"]}, headers=auth_headers
    )
    assert patch_res.status_code == 200

    res = await client.get("/api/recommendations/home", headers=auth_headers)
    assert res.status_code == 200
    categories = res.json()["data"]["categories"]
    trending = next((c for c in categories if c["category_type"] == "trending"), None)
    assert trending is not None
    assert trending["items"][0]["title"] == "Fallback Trending Track"


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
async def test_similar_songs_and_moods(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    songs = await seed_recommendation_catalog(db_session)
    serve_upstream(monkeypatch, songs)

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
