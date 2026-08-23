import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_preferences_defaults_headset_safety_reminder_on(client: AsyncClient, auth_headers: dict):
    res = await client.get("/api/users/preferences", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["data"]["headset_safety_reminder"] is True


@pytest.mark.asyncio
async def test_update_preferences_toggles_headset_safety_reminder(client: AsyncClient, auth_headers: dict):
    off_res = await client.patch(
        "/api/users/preferences", json={"headset_safety_reminder": False}, headers=auth_headers
    )
    assert off_res.status_code == 200
    assert off_res.json()["data"]["headset_safety_reminder"] is False

    get_res = await client.get("/api/users/preferences", headers=auth_headers)
    assert get_res.json()["data"]["headset_safety_reminder"] is False

    on_res = await client.patch(
        "/api/users/preferences", json={"headset_safety_reminder": True}, headers=auth_headers
    )
    assert on_res.json()["data"]["headset_safety_reminder"] is True


@pytest.mark.asyncio
async def test_update_preferences_leaves_other_fields_untouched(client: AsyncClient, auth_headers: dict):
    await client.patch("/api/users/preferences", json={"crossfade": 5}, headers=auth_headers)
    res = await client.patch(
        "/api/users/preferences", json={"headset_safety_reminder": False}, headers=auth_headers
    )
    data = res.json()["data"]
    assert data["crossfade"] == 5
    assert data["headset_safety_reminder"] is False
