"""First-run language/artist walkthrough.

`sync_user` already creates a `UserPreferences` row with sane defaults, so a
user is fully usable without ever calling these endpoints -- `completed`
simply tracks whether the client has shown (and the user has been through)
the guided flow, for the client to decide whether to show it on next launch.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.models.user import User
from app.schemas.onboarding import OnboardingArtistsRequest, OnboardingLanguagesRequest
from app.services.onboarding_service import onboarding_service
from app.utils.response import api_error, api_response

router = APIRouter(prefix="/api/onboarding", tags=["Onboarding"])


@router.get("/languages", summary="List selectable languages for onboarding")
async def get_languages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    languages = await onboarding_service.get_languages(db)
    return api_response([{"id": lang.id, "name": lang.name, "code": lang.code} for lang in languages])


@router.get("/artists/suggestions", summary="Suggested artists for onboarding artist selection")
async def get_suggested_artists(
    limit: int = Query(30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    artists = await onboarding_service.get_suggested_artists(db, user_id=current_user.id, limit=limit)
    return api_response([
        {
            "id": a.id,
            "name": a.name,
            "image_url": a.image_url,
            "song_count": a.song_count,
        }
        for a in artists
    ])


@router.get("/status", summary="Whether the current user has finished onboarding")
async def get_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    status = await onboarding_service.get_status(db, current_user.id)
    return api_response(status)


@router.post("/languages", summary="Set preferred languages during onboarding")
async def set_languages(
    req: OnboardingLanguagesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        status = await onboarding_service.set_languages(db, current_user.id, req.languages)
    except ValueError as e:
        return api_error("INVALID_LANGUAGES", str(e), status_code=422)
    return api_response(status)


@router.post("/artists", summary="Set favorite artists during onboarding")
async def set_artists(
    req: OnboardingArtistsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    status = await onboarding_service.set_artists(db, current_user.id, req.artist_ids)
    return api_response(status)


@router.post("/complete", summary="Mark onboarding as finished")
async def complete_onboarding(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    status = await onboarding_service.complete(db, current_user.id)
    return api_response(status)
