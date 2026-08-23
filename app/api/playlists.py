from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user, get_optional_user
from app.models.user import User
from app.schemas.playlist import PlaylistCreate, PlaylistUpdate, AddPlaylistSongRequest, ReorderPlaylistSongsRequest
from app.services.playlist_service import PlaylistService
from app.services.catalog_service import catalog_service
from app.utils.response import api_response, api_error

router = APIRouter(prefix="/api/playlists", tags=["Playlists"])


def _format_playlist_details(p) -> dict:
    songs = []
    if p.songs:
        for ps in p.songs:
            song_obj = None
            if ps.song:
                song_obj = {
                    "id": ps.song.id,
                    "external_id": ps.song.external_id,
                    "title": ps.song.title,
                    "artist_name": ps.song.artist_name,
                    "album_name": ps.song.album_name,
                    "duration": ps.song.duration,
                    "thumbnail_url": ps.song.thumbnail_url,
                    "audio_url": ps.song.audio_url,
                    "stream_urls": ps.song.stream_urls,
                    "language": ps.song.language,
                    "genre": ps.song.genre
                }
            songs.append({
                "id": ps.id,
                "playlist_id": ps.playlist_id,
                "song_id": ps.song_id,
                "position": ps.position,
                "added_at": ps.added_at.isoformat() if ps.added_at else "",
                "song": song_obj
            })

    return {
        "id": p.id,
        "user_id": p.user_id,
        "title": p.title,
        "description": p.description,
        "is_public": p.is_public,
        "is_collaborative": p.is_collaborative,
        "cover_url": p.cover_url,
        "created_at": p.created_at.isoformat() if p.created_at else "",
        "updated_at": p.updated_at.isoformat() if p.updated_at else "",
        "song_count": len(songs),
        "songs": songs
    }


@router.get("", summary="Get all playlists created by the user")
async def get_my_playlists(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    playlists = await PlaylistService.get_user_playlists(db, current_user.id, limit=limit, offset=offset)
    return api_response([_format_playlist_details(p) for p in playlists])


@router.post("", summary="Create a new playlist")
async def create_playlist(
    req: PlaylistCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    playlist = await PlaylistService.create_playlist(db, current_user.id, req)
    return api_response(_format_playlist_details(playlist), status_code=201)


@router.get("/{playlist_id}", summary="Get playlist details and songs")
async def get_playlist(
    playlist_id: str,
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id if current_user else None
    playlist = await PlaylistService.get_playlist(db, playlist_id, user_id=user_id)
    if not playlist:
        return api_error("PLAYLIST_NOT_FOUND", f"Playlist {playlist_id} not found or private", status_code=404)
    return api_response(_format_playlist_details(playlist))


@router.patch("/{playlist_id}", summary="Update playlist metadata")
async def update_playlist(
    playlist_id: str,
    req: PlaylistUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    playlist = await PlaylistService.update_playlist(db, playlist_id, current_user.id, req)
    if not playlist:
        return api_error("PLAYLIST_NOT_FOUND", "Playlist not found or you don't have permission to edit it", status_code=404)
    return api_response(_format_playlist_details(playlist))


@router.delete("/{playlist_id}", summary="Delete a playlist")
async def delete_playlist(
    playlist_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    success = await PlaylistService.delete_playlist(db, playlist_id, current_user.id)
    if not success:
        return api_error("PLAYLIST_NOT_FOUND", "Playlist not found or not owned by you", status_code=404)
    return api_response({"message": "Playlist deleted successfully"})


@router.post("/{playlist_id}/songs", summary="Add a song to playlist")
async def add_song(
    playlist_id: str,
    req: AddPlaylistSongRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    song = await catalog_service.get_song_by_id(db, req.song_id)
    if not song:
        return api_error("SONG_NOT_FOUND", f"Song {req.song_id} not found", status_code=404)

    try:
        entry = await PlaylistService.add_song(
            db=db,
            playlist_id=playlist_id,
            user_id=current_user.id,
            song_id=song.id,
            position=req.position
        )
        return api_response({
            "message": "Song added to playlist",
            "entry_id": entry.id,
            "playlist_id": entry.playlist_id,
            "song_id": entry.song_id,
            "position": entry.position
        }, status_code=201)
    except PermissionError as e:
        return api_error("FORBIDDEN", str(e), status_code=403)


@router.delete("/{playlist_id}/songs/{song_id}", summary="Remove a song from playlist")
async def remove_song(
    playlist_id: str,
    song_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        await PlaylistService.remove_song(
            db=db,
            playlist_id=playlist_id,
            user_id=current_user.id,
            song_id=song_id
        )
        return api_response({"message": "Song removed from playlist"})
    except PermissionError as e:
        return api_error("FORBIDDEN", str(e), status_code=403)


@router.patch("/{playlist_id}/reorder", summary="Reorder songs in playlist")
async def reorder_songs(
    playlist_id: str,
    req: ReorderPlaylistSongsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        await PlaylistService.reorder_songs(
            db=db,
            playlist_id=playlist_id,
            user_id=current_user.id,
            song_ids=req.song_ids
        )
        return api_response({"message": "Playlist reordered successfully"})
    except PermissionError as e:
        return api_error("FORBIDDEN", str(e), status_code=403)
    except KeyError as e:
        return api_error("SONG_NOT_IN_PLAYLIST", str(e).strip("'\""), status_code=400)
