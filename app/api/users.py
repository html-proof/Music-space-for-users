from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.models.user import User
from app.schemas.user import UserPreferencesUpdate
from app.services.user_service import UserService
from app.utils.response import api_response

router = APIRouter(prefix="/api/users", tags=["Users & Preferences"])


@router.get("/preferences", summary="Get current user preferences")
async def get_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    pref = await UserService.get_preferences(db, current_user.id)
    data = {
        "preferred_languages": pref.preferred_languages,
        "favorite_artists": pref.favorite_artists,
        "favorite_genres": pref.favorite_genres,
        "favorite_moods": pref.favorite_moods,
        "explicit_content": pref.explicit_content,
        "audio_quality": pref.audio_quality,
        "autoplay": pref.autoplay,
        "crossfade": pref.crossfade,
        "data_saver": pref.data_saver,
        "headset_safety_reminder": pref.headset_safety_reminder
    }
    return api_response(data)


@router.patch("/preferences", summary="Update user preferences")
async def update_preferences(
    updates: UserPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    pref = await UserService.update_preferences(db, current_user.id, updates)
    data = {
        "preferred_languages": pref.preferred_languages,
        "favorite_artists": pref.favorite_artists,
        "favorite_genres": pref.favorite_genres,
        "favorite_moods": pref.favorite_moods,
        "explicit_content": pref.explicit_content,
        "audio_quality": pref.audio_quality,
        "autoplay": pref.autoplay,
        "crossfade": pref.crossfade,
        "data_saver": pref.data_saver,
        "headset_safety_reminder": pref.headset_safety_reminder
    }
    return api_response(data)


@router.get("/analytics", summary="Get user listening behavior and analytics profile")
async def get_analytics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    analytics = await UserService.get_analytics(db, current_user.id)
    return api_response(analytics.model_dump())
