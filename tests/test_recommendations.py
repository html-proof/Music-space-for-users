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
