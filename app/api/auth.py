from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.models.user import User
from app.schemas.auth import UserProfileResponse, SyncUserRequest
from app.services.auth_service import AuthService
from app.utils.response import api_response

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.get("/me", summary="Retrieve authenticated user profile")
async def get_me(current_user: User = Depends(get_current_user)):
    data = {
        "id": current_user.id,
        "firebase_uid": current_user.firebase_uid,
        "email": current_user.email,
        "display_name": current_user.display_name,
        "photo_url": current_user.photo_url,
        "country": current_user.country,
        "language": current_user.language,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else "",
        "last_login": current_user.last_login_at.isoformat() if current_user.last_login_at else ""
    }
    return api_response(data)


@router.post("/sync", summary="Synchronize user profile from Firebase")
async def sync_profile(
    req: SyncUserRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    updated_user = await AuthService.sync_user(
        db=db,
        firebase_payload={"uid": current_user.firebase_uid, "email": current_user.email},
        sync_req=req
    )
    data = {
        "id": updated_user.id,
        "firebase_uid": updated_user.firebase_uid,
        "email": updated_user.email,
        "display_name": updated_user.display_name,
        "photo_url": updated_user.photo_url,
        "country": updated_user.country,
        "language": updated_user.language,
        "created_at": updated_user.created_at.isoformat() if updated_user.created_at else "",
        "last_login": updated_user.last_login_at.isoformat() if updated_user.last_login_at else ""
    }
    return api_response(data)


@router.delete("/account", summary="Delete user account and all personal data")
async def delete_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await AuthService.delete_user_account(db, current_user)
    return api_response({"message": "Account successfully deleted"})
