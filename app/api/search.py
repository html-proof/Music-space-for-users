from fastapi import APIRouter, Depends, Query
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user, get_optional_user
from app.models.user import User
from app.services.catalog_service import catalog_service
from app.services.history_service import HistoryService
from app.services.search_service import search_service
from app.utils.response import api_response

router = APIRouter(prefix="/api/search", tags=["Search & History"])


SEARCH_TYPES = ("all", "track", "album", "artist")


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


def _format_album(a) -> dict:
    return {
        "id": a.id,
        "external_id": a.external_id,
        "seokey": a.seokey,
        "title": a.title,
        "artist_id": a.artist_id,
        "artist_name": a.artist_name,
        "cover_url": a.cover_url,
        "language": a.language,
        "release_date": a.release_date,
        "track_count": a.track_count
    }


def _format_artist(a) -> dict:
    return {
        "id": a.id,
        "external_id": a.external_id,
        "seokey": a.seokey,
        "name": a.name,
        "image_url": a.image_url,
        "genres": a.genres,
        "song_count": a.song_count,
        "album_count": a.album_count
    }


@router.get("", summary="Search across songs, artists, and albums")
async def search_catalog(
    query: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=50),
    type: str = Query(
        "all",
        pattern=f"^({'|'.join(SEARCH_TYPES)})$",
        description="Restrict results to one kind: " + ", ".join(SEARCH_TYPES)
    ),
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db)
):
    """
    `type=all` searches every kind, which costs one upstream call per kind --
    pass a narrower `type` when the client only renders one section.
    """
    # Log search for recommendation signals if authenticated
    if current_user:
        await HistoryService.log_search(db, current_user.id, query, result_type=type)

    songs, albums, artists = [], [], []
    # Sequentially, not gathered: a single AsyncSession cannot be shared across
    # concurrent tasks.
    if type in ("all", "track"):
        songs = await search_service.search_songs(
            db, query, limit=limit, user_id=current_user.id if current_user else None
        )
    if type in ("all", "album"):
        albums = await catalog_service.search_albums(db, query, limit=limit)
    if type in ("all", "artist"):
        artists = await catalog_service.search_artists(db, query, limit=limit)

    return api_response({
        "query": query,
        "type": type,
        # `songs` stays the canonical key for track results; existing clients
        # read it and must keep working.
        "songs": [_format_song(s) for s in songs],
        "albums": [_format_album(a) for a in albums],
        "artists": [_format_artist(a) for a in artists],
        "total": len(songs) + len(albums) + len(artists)
    })


@router.get("/suggest", summary="Autocomplete suggestions for a partial query")
async def search_suggest(
    query: str = Query(..., min_length=1, max_length=100, description="Partial query"),
    limit: int = Query(10, ge=1, le=20),
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Local-only, so it is safe to call on every keystroke: no upstream round trip,
    and the query is not written to search history (a half-typed query is not a
    search the user made).
    """
    suggestions = await search_service.suggest(
        db, query, limit=limit, user_id=current_user.id if current_user else None
    )
    return api_response({"query": query, "suggestions": suggestions, "total": len(suggestions)})


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
