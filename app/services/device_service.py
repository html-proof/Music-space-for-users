import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.device import Device, UserSession
from app.schemas.device import DeviceRegisterRequest, DeviceHeartbeatRequest
from app.utils.security import generate_session_token, hash_token

logger = logging.getLogger("device_service")


class DeviceService:
    @staticmethod
    async def get_user_devices(db: AsyncSession, user_id: str) -> List[Device]:
        stmt = select(Device).where(Device.user_id == user_id).order_by(Device.last_active_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def register_device(
        db: AsyncSession,
        user_id: str,
        req: DeviceRegisterRequest,
        ip_address: Optional[str] = None
    ) -> Tuple[Device, UserSession, str]:
        now = datetime.now(timezone.utc)
        stmt = select(Device).where(and_(Device.user_id == user_id, Device.device_id == req.device_id))
        result = await db.execute(stmt)
        device = result.scalar_one_or_none()

        if device:
            device.device_name = req.device_name
            device.device_type = req.device_type
            device.platform = req.platform
            device.os_version = req.os_version
            device.app_version = req.app_version
            device.ip_address = ip_address or device.ip_address
            device.last_active_at = now
            device.is_online = True
            if req.push_token:
                device.push_token = req.push_token
        else:
            device = Device(
                user_id=user_id,
                device_id=req.device_id,
                device_name=req.device_name,
                device_type=req.device_type,
                platform=req.platform,
                os_version=req.os_version,
                app_version=req.app_version,
                ip_address=ip_address,
                last_active_at=now,
                is_online=True,
                push_token=req.push_token
            )
            db.add(device)

        # Create new session
        raw_token = generate_session_token()
        token_hash = hash_token(raw_token)
        session = UserSession(
            user_id=user_id,
            device_id=req.device_id,
            session_token_hash=token_hash,
            started_at=now,
            last_activity_at=now,
            is_active=True
        )
        db.add(session)

        await db.commit()
        await db.refresh(device)
        await db.refresh(session)
        logger.info(f"Registered device {req.device_name} ({req.device_id}) for user {user_id}")
        return device, session, raw_token

    @staticmethod
    async def heartbeat(
        db: AsyncSession,
        user_id: str,
        device_id: str,
        req: DeviceHeartbeatRequest
    ) -> Optional[Device]:
        stmt = select(Device).where(and_(Device.user_id == user_id, Device.device_id == device_id))
        result = await db.execute(stmt)
        device = result.scalar_one_or_none()
        if not device:
            return None

        now = datetime.now(timezone.utc)
        device.last_active_at = now
        device.is_online = req.is_online
        if req.ip_address:
            device.ip_address = req.ip_address

        # Update session activity
        session_stmt = (
            select(UserSession)
            .where(and_(UserSession.user_id == user_id, UserSession.device_id == device_id, UserSession.is_active == True))
            .order_by(UserSession.started_at.desc())
        )
        session_res = await db.execute(session_stmt)
        session = session_res.scalars().first()
        if session:
            session.last_activity_at = now

        await db.commit()
        await db.refresh(device)
        return device

    @staticmethod
    async def remove_device(db: AsyncSession, user_id: str, device_id: str) -> bool:
        stmt = select(Device).where(and_(Device.user_id == user_id, Device.device_id == device_id))
        result = await db.execute(stmt)
        device = result.scalar_one_or_none()
        if not device:
            return False

        # Deactivate all sessions for this device
        session_stmt = select(UserSession).where(and_(UserSession.user_id == user_id, UserSession.device_id == device_id))
        session_res = await db.execute(session_stmt)
        for s in session_res.scalars().all():
            s.is_active = False
            s.ended_at = datetime.now(timezone.utc)

        await db.delete(device)
        await db.commit()
        logger.info(f"Removed device {device_id} for user {user_id}")
        return True


device_service = DeviceService()
