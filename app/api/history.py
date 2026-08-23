from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.models.user import User
from app.services.history_service import HistoryService
from app.utils.response import api_response

router = APIRouter(prefix="/api/history", tags=["Listening History"])


def _format_history_entry(hist) -> dict:
    song_data = None
    if hist.song:
        song_data = {
            "id": hist.song.id,
            "external_id": hist.song.external_id,
            "title": hist.song.title,
            "artist_name": hist.song.artist_name,
            "album_name": hist.song.album_name,
            "duration": hist.song.duration,
            "thumbnail_url": hist.song.thumbnail_url,
            "stream_urls": hist.song.stream_urls,
            "language": hist.song.language,
            "genre": hist.song.genre
        }
    return {
        "id": hist.id,
        "song_id": hist.song_id,
        "device_id": hist.device_id,
        "session_id": hist.session_id,
        "started_at": hist.started_at.isoformat() if hist.started_at else "",
        "ended_at": hist.ended_at.isoformat() if hist.ended_at else "",
        "duration_listened": hist.duration_listened,
        "completion_percentage": hist.completion_percentage,
        "skipped": hist.skipped,
        "source": hist.source,
        "song": song_data
    }


@router.get("", summary="Get paginated listening history")
async def get_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    entries = await HistoryService.get_history(db, current_user.id, limit=limit, offset=offset)
    data = [_format_history_entry(e) for e in entries]
    return api_response({"items": data, "limit": limit, "offset": offset, "total": len(data)})


@router.get("/recent", summary="Get recently listened tracks")
async def get_recent_history(
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    entries = await HistoryService.get_recent_history(db, current_user.id, limit=limit)
    data = [_format_history_entry(e) for e in entries]
    return api_response(data)


@router.delete("", summary="Clear all listening history")
async def clear_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await HistoryService.clear_history(db, current_user.id)
    return api_response({"message": "Listening history cleared successfully"})
