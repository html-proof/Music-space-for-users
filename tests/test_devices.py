import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_device_registration_and_list(client: AsyncClient, auth_headers: dict):
    # Register Device 1 (Phone)
    dev1 = {
        "device_id": "phone-123",
        "device_name": "Sebastian's iPhone",
        "device_type": "mobile",
        "platform": "iOS",
        "os_version": "17.4",
        "app_version": "2.1.0"
    }
    res1 = await client.post("/api/devices/register", json=dev1, headers=auth_headers)
    assert res1.status_code == 201
    data1 = res1.json()["data"]
    assert data1["device"]["device_id"] == "phone-123"
    assert "session_token" in data1

    # Register Device 2 (Laptop)
    dev2 = {
        "device_id": "laptop-456",
        "device_name": "Windows PC",
        "device_type": "desktop",
        "platform": "Windows",
        "os_version": "11",
        "app_version": "2.1.0"
    }
    res2 = await client.post("/api/devices/register", json=dev2, headers=auth_headers)
    assert res2.status_code == 201

    # List Devices
    list_res = await client.get("/api/devices", headers=auth_headers)
    assert list_res.status_code == 200
    devices = list_res.json()["data"]
    assert len(devices) == 2
    device_names = [d["device_name"] for d in devices]
    assert "Sebastian's iPhone" in device_names
    assert "Windows PC" in device_names


@pytest.mark.asyncio
async def test_device_heartbeat(client: AsyncClient, auth_headers: dict):
    # Register device
    dev = {
        "device_id": "phone-hb",
        "device_name": "iPhone HB",
        "device_type": "mobile"
    }
    await client.post("/api/devices/register", json=dev, headers=auth_headers)

    # Heartbeat
    hb_res = await client.post(
        "/api/devices/phone-hb/heartbeat",
        json={"is_online": True},
        headers=auth_headers
    )
    assert hb_res.status_code == 200
    assert hb_res.json()["data"]["is_online"] is True


@pytest.mark.asyncio
async def test_device_removal(client: AsyncClient, auth_headers: dict):
    dev = {
        "device_id": "tablet-789",
        "device_name": "iPad Pro",
        "device_type": "tablet"
    }
    await client.post("/api/devices/register", json=dev, headers=auth_headers)

    # Delete device
    del_res = await client.delete("/api/devices/tablet-789", headers=auth_headers)
    assert del_res.status_code == 200

    # Verify deleted
    list_res = await client.get("/api/devices", headers=auth_headers)
    devices = list_res.json()["data"]
    assert all(d["device_id"] != "tablet-789" for d in devices)
