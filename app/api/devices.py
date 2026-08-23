from typing import Optional
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.models.user import User
from app.schemas.device import DeviceRegisterRequest, DeviceHeartbeatRequest
from app.services.device_service import DeviceService
from app.utils.response import api_response, api_error

router = APIRouter(prefix="/api/devices", tags=["Devices & Sessions"])


@router.get("", summary="List all registered devices for user")
async def get_devices(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    devices = await DeviceService.get_user_devices(db, current_user.id)
    data = [
        {
            "id": d.id,
            "device_id": d.device_id,
            "device_name": d.device_name,
            "device_type": d.device_type,
            "platform": d.platform,
            "os_version": d.os_version,
            "app_version": d.app_version,
            "ip_address": d.ip_address,
            "last_active_at": d.last_active_at.isoformat() if d.last_active_at else "",
            "is_online": d.is_online,
            "push_token": d.push_token,
            "created_at": d.created_at.isoformat() if d.created_at else "",
            "updated_at": d.updated_at.isoformat() if d.updated_at else ""
        }
        for d in devices
    ]
    return api_response(data)


@router.post("/register", summary="Register or update a device")
async def register_device(
    req: DeviceRegisterRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    client_ip = request.client.host if request.client else None
    device, session, raw_token = await DeviceService.register_device(
        db=db,
        user_id=current_user.id,
        req=req,
        ip_address=client_ip
    )
    data = {
        "device": {
            "id": device.id,
            "device_id": device.device_id,
            "device_name": device.device_name,
            "device_type": device.device_type,
            "platform": device.platform,
            "is_online": device.is_online,
            "last_active_at": device.last_active_at.isoformat() if device.last_active_at else ""
        },
        "session_token": raw_token
    }
    return api_response(data, status_code=201)


@router.post("/{device_id}/heartbeat", summary="Send device heartbeat to keep online status")
async def heartbeat(
    device_id: str,
    request: Request,
    req: Optional[DeviceHeartbeatRequest] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # The body is optional: a bare ping is the documented usage, and all its
    # fields already have defaults.
    req = req or DeviceHeartbeatRequest()
    if not req.ip_address and request.client:
        req.ip_address = request.client.host
    device = await DeviceService.heartbeat(db, current_user.id, device_id, req)
    if not device:
        return api_error("DEVICE_NOT_FOUND", f"Device {device_id} not found", status_code=404)
    return api_response({
        "device_id": device.device_id,
        "is_online": device.is_online,
        "last_active_at": device.last_active_at.isoformat()
    })


@router.delete("/{device_id}", summary="Remove or logout another device")
async def remove_device(
    device_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    success = await DeviceService.remove_device(db, current_user.id, device_id)
    if not success:
        return api_error("DEVICE_NOT_FOUND", f"Device {device_id} not found", status_code=404)
    return api_response({"message": f"Device {device_id} removed successfully"})
