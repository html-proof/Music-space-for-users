from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user, get_optional_user
from app.models.user import User
from app.services.recommendation_service import RecommendationService
from app.services.catalog_service import catalog_service
from app.utils.response import api_response

router = APIRouter(prefix="/api/recommendations", tags=["Recommendations"])


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


@router.get("", summary="Get personalized recommendations home feed")
@router.get("/home", summary="Get personalized recommendations home feed")
async def get_home_recommendations(
    refresh: bool = Query(
        False,
        description="Bypass the cached personalized ranking and recompute it now "
                    "(the mobile client's pull-to-refresh sets this)."
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    recs = await RecommendationService.get_home_recommendations(
        db=db,
        user_id=current_user.id,
        user_name=current_user.display_name or "Friend",
        refresh=refresh,
    )

    data = {
        "greeting": recs.greeting,
        "top_mix": [_format_song(s) for s in recs.top_mix] if recs.top_mix else [],
        "categories": [
            {
                "id": c.id,
                "title": c.title,
                "description": c.description,
                "category_type": c.category_type,
                "items": [_format_song(s) for s in c.items]
            }
            for c in recs.categories
        ]
    }
    return api_response(data)


@router.get("/similar-song/{song_id}", summary="Get songs similar to a given song")
async def get_similar_songs(
    song_id: str,
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db)
):
    songs = await RecommendationService.get_similar_songs(db, song_id, limit=limit)
    return api_response([_format_song(s) for s in songs])


@router.get("/similar-artist/{artist_id}", summary="Get artists similar to a given artist")
async def get_similar_artists(
    artist_id: str,
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db)
):
    raw = await catalog_service.gaana.get_similar_artists(artist_id, limit)
    similar_artists = []
    if isinstance(raw, list):
        for a in raw:
            if isinstance(a, dict) and "seokey" in a:
                similar_artists.append({
                    "id": a.get("artist_id", ""),
                    "external_id": a.get("seokey", ""),
                    "name": a.get("name", ""),
                    "seokey": a.get("seokey"),
                    "image_url": a.get("images", {}).get("urls", {}).get("large_artwork", ""),
                    "song_count": int(a.get("song_count", 0)) if a.get("song_count") else 0,
                    "album_count": int(a.get("album_count", 0)) if a.get("album_count") else 0
                })
    return api_response(similar_artists)


@router.get("/mood/{mood}", summary="Get songs tailored to a specific mood")
async def get_mood_mix(
    mood: str = Path(..., description="Chill, Workout, Focus, Party, Romantic, Happy, Sad"),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db)
):
    songs = await RecommendationService.get_mood_mix(db, mood, limit=limit)
    return api_response([_format_song(s) for s in songs])
