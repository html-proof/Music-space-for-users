from fastapi import APIRouter, Depends, Query
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user, get_optional_user
from app.models.user import User
from app.services.catalog_service import catalog_service
from app.services.history_service import HistoryService
from app.utils.response import api_response

router = APIRouter(prefix="/api/search", tags=["Search & History"])


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
        "audio_url": s.audio_url,
        "stream_urls": s.stream_urls,
        "language": s.language,
        "genre": s.genre,
        "is_explicit": s.is_explicit
    }


@router.get("", summary="Search across songs, artists, and albums")
async def search_catalog(
    query: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=50),
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db)
):
    # Log search for recommendation signals if authenticated
    if current_user:
        await HistoryService.log_search(db, current_user.id, query, result_type="all")

    songs = await catalog_service.search_songs(db, query, limit=limit)
    return api_response({
        "query": query,
        "songs": [_format_song(s) for s in songs],
        "total": len(songs)
    })


@router.get("/history", summary="Get user's recent search queries")
async def get_search_history(
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    entries = await HistoryService.get_search_history(db, current_user.id, limit=limit)
    data = [
        {
            "id": e.id,
            "query": e.query,
            "result_type": e.result_type,
            "timestamp": e.timestamp.isoformat() if e.timestamp else ""
        }
        for e in entries
    ]
    return api_response(data)


@router.delete("/history", summary="Clear search query history")
async def clear_search_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await HistoryService.clear_search_history(db, current_user.id)
    return api_response({"message": "Search history cleared successfully"})
