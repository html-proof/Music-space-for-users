from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.models.user import User
from app.services.library_service import LibraryService
from app.services.catalog_service import catalog_service
from app.utils.response import api_response, api_error

router = APIRouter(prefix="/api", tags=["Library & Favorites"])


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
        "is_explicit": s.is_explicit,
        "is_liked": True
    }


def _format_album(a) -> dict:
    return {
        "id": a.id,
        "external_id": a.external_id,
        "title": a.title,
        "artist_id": a.artist_id,
        "artist_name": a.artist_name,
        "cover_url": a.cover_url,
        "release_date": a.release_date,
        "language": a.language,
        "track_count": a.track_count,
        "is_saved": True
    }


def _format_artist(art) -> dict:
    return {
        "id": art.id,
        "external_id": art.external_id,
        "name": art.name,
        "seokey": art.seokey,
        "image_url": art.image_url,
        "genres": art.genres,
        "song_count": art.song_count,
        "album_count": art.album_count,
        "is_followed": True
    }


# Liked Songs
@router.post("/songs/{song_id}/like", summary="Like a song")
async def like_song(
    song_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    song = await catalog_service.get_song_by_id(db, song_id)
    if not song:
        return api_error("SONG_NOT_FOUND", f"Song {song_id} not found", status_code=404)
    await LibraryService.like_song(db, current_user.id, song.id)
    return api_response({"message": "Song liked", "song_id": song.id, "is_liked": True})


@router.delete("/songs/{song_id}/like", summary="Unlike a song")
async def unlike_song(
    song_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    song = await catalog_service.get_song_by_id(db, song_id)
    target_id = song.id if song else song_id
    await LibraryService.unlike_song(db, current_user.id, target_id)
    return api_response({"message": "Song unliked", "song_id": target_id, "is_liked": False})


@router.get("/library/liked", summary="Get user's liked songs collection")
async def get_liked_songs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    songs = await LibraryService.get_liked_songs(db, current_user.id, limit=limit, offset=offset)
    return api_response([_format_song(s) for s in songs])


# Saved Albums
@router.post("/albums/{album_id}/save", summary="Save an album to library")
async def save_album(
    album_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await LibraryService.save_album(db, current_user.id, album_id)
    return api_response({"message": "Album saved to library", "album_id": album_id, "is_saved": True})


@router.delete("/albums/{album_id}/save", summary="Remove an album from library")
async def unsave_album(
    album_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await LibraryService.unsave_album(db, current_user.id, album_id)
    return api_response({"message": "Album removed from library", "album_id": album_id, "is_saved": False})


@router.get("/library/albums", summary="Get user's saved albums")
async def get_saved_albums(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    albums = await LibraryService.get_saved_albums(db, current_user.id, limit=limit, offset=offset)
    return api_response([_format_album(a) for a in albums])


# Followed Artists
@router.post("/artists/{artist_id}/follow", summary="Follow an artist")
async def follow_artist(
    artist_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await LibraryService.follow_artist(db, current_user.id, artist_id)
    return api_response({"message": "Artist followed", "artist_id": artist_id, "is_followed": True})


@router.delete("/artists/{artist_id}/follow", summary="Unfollow an artist")
async def unfollow_artist(
    artist_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await LibraryService.unfollow_artist(db, current_user.id, artist_id)
    return api_response({"message": "Artist unfollowed", "artist_id": artist_id, "is_followed": False})


@router.get("/library/artists", summary="Get user's followed artists")
async def get_followed_artists(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    artists = await LibraryService.get_followed_artists(db, current_user.id, limit=limit, offset=offset)
    return api_response([_format_artist(a) for a in artists])
