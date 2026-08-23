import logging
from typing import Optional
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.config.firebase import verify_firebase_token
from app.services.auth_service import AuthService
from app.models.user import User

logger = logging.getLogger("auth_middleware")
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Dependency that verifies Firebase ID token from Authorization header
    and retrieves the corresponding User from the database.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authentication token is missing"}
        )

    token = credentials.credentials
    try:
        payload = verify_firebase_token(token)
    except Exception as e:
        # Logged with the reason; the response stays generic so verifier
        # internals are not disclosed to the caller.
        logger.warning(f"Unauthorized token verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Invalid or expired authentication token"}
        )

    uid = payload.get("uid")
    if not uid:
        logger.warning("Verified token contained no uid claim.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Invalid or expired authentication token"}
        )

    user = await AuthService.get_user_by_firebase_uid(db, uid)
    if not user:
        # Auto-sync/create user record on first authenticated call
        user = await AuthService.sync_user(db, payload)

    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """Dependency that returns current user if token is provided, else None."""
    if not credentials or not credentials.credentials:
        return None
    try:
        payload = verify_firebase_token(credentials.credentials)
        user = await AuthService.get_user_by_firebase_uid(db, payload["uid"])
        if not user:
            user = await AuthService.sync_user(db, payload)
        return user
    except Exception:
        return None
