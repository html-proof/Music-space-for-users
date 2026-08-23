from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.models.user import User
from app.services.stats_service import RANGE_DAYS, StatsService
from app.utils.response import api_response

router = APIRouter(prefix="/api/me", tags=["Listening Stats"])

_RANGE_PATTERN = f"^({'|'.join(RANGE_DAYS)})$"
# `range` is a builtin, so the parameter is named range_ and aliased back.
_RANGE_QUERY = Query(
    "4weeks",
    alias="range",
    pattern=_RANGE_PATTERN,
    description="Window to aggregate over: " + ", ".join(RANGE_DAYS)
)


def _format_song(s) -> dict:
    return {
        "id": s.id,
        "external_id": s.external_id,
        "title": s.title,
        "artist_id": s.artist_id,
        "artist_name": s.artist_name,
        "album_id": s.album_id,
        "album_name": s.album_name,
        "duration": s.duration,
        "thumbnail_url": s.thumbnail_url,
        "language": s.language,
        "genre": s.genre,
        "is_explicit": s.is_explicit
    }


@router.get("/top/tracks", summary="Get the user's most played tracks")
async def top_tracks(
    range_: str = _RANGE_QUERY,
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    rows = await StatsService.top_tracks(db, current_user.id, range_key=range_, limit=limit)
    return api_response({
        "range": range_,
        "items": [
            {
                "rank": row["rank"],
                "play_count": row["play_count"],
                "seconds_listened": row["seconds_listened"],
                "song": _format_song(row["song"])
            }
            for row in rows
        ],
        "total": len(rows)
    })


@router.get("/top/artists", summary="Get the user's most played artists")
async def top_artists(
    range_: str = _RANGE_QUERY,
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    rows = await StatsService.top_artists(db, current_user.id, range_key=range_, limit=limit)
    return api_response({"range": range_, "items": rows, "total": len(rows)})


@router.get("/stats", summary="Get the user's listening summary")
async def listening_stats(
    range_: str = _RANGE_QUERY,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    return api_response(await StatsService.summary(db, current_user.id, range_key=range_))
