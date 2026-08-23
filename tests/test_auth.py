import base64
import json
import pytest
from httpx import AsyncClient


def _mock_token(uid: str, sign_in_provider: str) -> str:
    payload = {
        "uid": uid,
        "email": f"{uid}@example.com",
        "firebase": {"sign_in_provider": sign_in_provider},
    }
    return "mock_" + base64.b64encode(json.dumps(payload).encode()).decode()


@pytest.mark.asyncio
async def test_auth_me_rejects_password_provider(client: AsyncClient):
    headers = {"Authorization": f"Bearer {_mock_token('pwuser', 'password')}"}
    response = await client.get("/api/auth/me", headers=headers)
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "PROVIDER_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_auth_me_accepts_google_provider(client: AsyncClient):
    headers = {"Authorization": f"Bearer {_mock_token('guser', 'google.com')}"}
    response = await client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.asyncio
async def test_auth_me_unauthorized(client: AsyncClient):
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_auth_me_success(client: AsyncClient, auth_headers: dict):
    response = await client.get("/api/auth/me", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["firebase_uid"] == "user123"
    assert "email" in data["data"]


@pytest.mark.asyncio
async def test_auth_sync_profile(client: AsyncClient, auth_headers: dict):
    payload = {
        "display_name": "Sebastian Bach",
        "country": "US",
        "language": "English"
    }
    response = await client.post("/api/auth/sync", json=payload, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["display_name"] == "Sebastian Bach"
    assert data["data"]["country"] == "US"


@pytest.mark.asyncio
async def test_auth_delete_account(client: AsyncClient, auth_headers: dict):
    # Call me to create user
    await client.get("/api/auth/me", headers=auth_headers)

    # Delete account
    del_res = await client.delete("/api/auth/account", headers=auth_headers)
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # User re-registers cleanly on next call
    new_res = await client.get("/api/auth/me", headers=auth_headers)
    assert new_res.status_code == 200
