import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import Artist


async def seed_artists(db: AsyncSession):
    a1 = Artist(external_id="art-ob-1", name="Daft Punk")
    a2 = Artist(external_id="art-ob-2", name="Justice")
    db.add(a1)
    db.add(a2)
    await db.commit()
    await db.refresh(a1)
    await db.refresh(a2)
    return a1, a2


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
    # sync_user seeds preferred_languages with a default, not an empty list
    assert isinstance(data["preferred_languages"], list)
    assert data["favorite_artists"] == []


@pytest.mark.asyncio
async def test_set_languages(client: AsyncClient, auth_headers: dict):
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
async def test_set_artists_updates_preferences_and_follows(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    a1, a2 = await seed_artists(db_session)

    res = await client.post(
        "/api/onboarding/artists", json={"artist_ids": [a1.id, a2.id]}, headers=auth_headers
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert set(data["favorite_artists"]) == {"Daft Punk", "Justice"}

    followed_res = await client.get("/api/library/artists", headers=auth_headers)
    followed_names = {a["name"] for a in followed_res.json()["data"]}
    assert followed_names == {"Daft Punk", "Justice"}


@pytest.mark.asyncio
async def test_set_artists_skips_unknown_ids(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    a1, _ = await seed_artists(db_session)

    res = await client.post(
        "/api/onboarding/artists", json={"artist_ids": [a1.id, "not-a-real-id"]}, headers=auth_headers
    )
    assert res.status_code == 200
    assert res.json()["data"]["favorite_artists"] == ["Daft Punk"]


@pytest.mark.asyncio
async def test_complete_flow(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    a1, _ = await seed_artists(db_session)

    await client.post("/api/onboarding/languages", json={"languages": ["English"]}, headers=auth_headers)
    await client.post("/api/onboarding/artists", json={"artist_ids": [a1.id]}, headers=auth_headers)

    complete_res = await client.post("/api/onboarding/complete", headers=auth_headers)
    assert complete_res.status_code == 200
    data = complete_res.json()["data"]
    assert data["completed"] is True
    assert data["completed_at"] is not None

    status_res = await client.get("/api/onboarding/status", headers=auth_headers)
    assert status_res.json()["data"]["completed"] is True
