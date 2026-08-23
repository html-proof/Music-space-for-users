"""Lyrics retrieval and curation.

`GET /api/songs/{song_id}/lyrics` is open to any authenticated user, same as
the rest of the catalog. Writes are guarded by the `LYRICS_ADMIN_TOKEN` shared
secret -- there is no admin role in firebase_auth.py, and there is no licensed
lyrics provider wired in yet, so lyrics are curated out-of-band and pushed
through this endpoint rather than scraped or fetched from an unauthorized
source.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.models.user import User
from app.schemas.lyrics import LyricsUpsertRequest
from app.services.lyrics_service import lyrics_service
from app.utils.response import api_error, api_response
from app.utils.security import constant_time_equals

logger = logging.getLogger("lyrics_api")

router = APIRouter(prefix="/api/songs", tags=["Lyrics"])


def _authorize(token: Optional[str]) -> Optional[str]:
    """Return an error code, or None when the request may proceed."""
    configured = (settings.LYRICS_ADMIN_TOKEN or "").strip()
    if not configured:
        return "not_configured"
    supplied = (token or "").strip()
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    if not constant_time_equals(supplied, configured):
        return "forbidden"
    return None


@router.get("/{song_id}/lyrics", summary="Retrieve lyrics for a song")
async def get_lyrics(
    song_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    data = await lyrics_service.get_lyrics(db, song_id)
    if data is None:
        return api_error("NOT_FOUND", "Song not found", status_code=404)
    return api_response(data)


@router.put("/{song_id}/lyrics", summary="Create or replace lyrics for a song (admin token required)")
async def upsert_lyrics(
    song_id: str,
    req: LyricsUpsertRequest,
    x_lyrics_admin_token: Optional[str] = Header(default=None, alias="X-Lyrics-Admin-Token"),
    db: AsyncSession = Depends(get_db),
):
    denial = _authorize(x_lyrics_admin_token)
    if denial == "not_configured":
        return api_error(
            "lyrics_write_disabled",
            "LYRICS_ADMIN_TOKEN is not set, so writing lyrics over HTTP is disabled.",
            status_code=503,
        )
    if denial:
        return api_error("forbidden", "Invalid lyrics admin token", status_code=403)

    if not req.plain_text and not req.synced_lines:
        return api_error(
            "invalid_request",
            "At least one of plain_text or synced_lines is required.",
            status_code=422,
        )

    data = await lyrics_service.upsert_lyrics(
        db,
        song_id,
        plain_text=req.plain_text,
        synced_lines=req.synced_lines,
        language=req.language,
        source=req.source,
    )
    if data is None:
        return api_error("NOT_FOUND", "Song not found", status_code=404)
    return api_response(data)


@router.delete("/{song_id}/lyrics", summary="Delete lyrics for a song (admin token required)")
async def delete_lyrics(
    song_id: str,
    x_lyrics_admin_token: Optional[str] = Header(default=None, alias="X-Lyrics-Admin-Token"),
    db: AsyncSession = Depends(get_db),
):
    denial = _authorize(x_lyrics_admin_token)
    if denial == "not_configured":
        return api_error(
            "lyrics_write_disabled",
            "LYRICS_ADMIN_TOKEN is not set, so writing lyrics over HTTP is disabled.",
            status_code=503,
        )
    if denial:
        return api_error("forbidden", "Invalid lyrics admin token", status_code=403)

    deleted = await lyrics_service.delete_lyrics(db, song_id)
    if not deleted:
        return api_error("NOT_FOUND", "Lyrics not found for this song", status_code=404)
    return api_response({"message": "Lyrics deleted"})
