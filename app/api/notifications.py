from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.models.user import User
from app.schemas.notification import NotificationPreferencesUpdate
from app.services.notification_service import notification_service
from app.utils.response import api_error, api_response

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@router.get("", summary="List the user's notifications")
async def list_notifications(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    data = await notification_service.list_notifications(db, current_user.id, limit=limit, offset=offset)
    return api_response(data)


@router.patch("/{notification_id}/read", summary="Mark a notification as read")
async def mark_notification_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    found = await notification_service.mark_read(db, current_user.id, notification_id)
    if not found:
        return api_error("NOT_FOUND", "Notification not found", status_code=404)
    return api_response({"message": "Marked as read"})


@router.get("/preferences", summary="Get notification preferences")
async def get_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    prefs = await notification_service.get_preferences(db, current_user.id)
    return api_response({"new_release_songs": prefs.new_release_songs})


@router.patch("/preferences", summary="Update notification preferences")
async def update_preferences(
    updates: NotificationPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    prefs = await notification_service.update_preferences(db, current_user.id, updates)
    return api_response({"new_release_songs": prefs.new_release_songs})
