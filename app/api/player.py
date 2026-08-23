from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.models.user import User
from app.schemas.playback import PlayRequest, PauseRequest, ResumeRequest, SeekRequest, SyncPlaybackRequest, PlaybackEventRequest
from app.services.playback_service import PlaybackService
from app.utils.response import api_response, api_error

router = APIRouter(prefix="/api/player", tags=["Player & Playback"])


def _format_playback_state(playback) -> dict:
    song_data = None
    if playback.song:
        song_data = {
            "id": playback.song.id,
            "external_id": playback.song.external_id,
            "title": playback.song.title,
            "artist_name": playback.song.artist_name,
            "album_name": playback.song.album_name,
            "duration": playback.song.duration,
            "thumbnail_url": playback.song.thumbnail_url,
            "audio_url": playback.song.audio_url,
            "stream_urls": playback.song.stream_urls,
            "language": playback.song.language,
            "genre": playback.song.genre,
            "mood": playback.song.mood,
            "is_explicit": playback.song.is_explicit
        }

    return {
        "user_id": playback.user_id,
        "device_id": playback.device_id,
        "song_id": playback.song_id,
        "playlist_id": playback.playlist_id,
        "position_seconds": playback.position_seconds,
        "duration_seconds": playback.duration_seconds,
        "state": playback.state,
        "volume": playback.volume,
        "shuffle": playback.shuffle,
        "repeat_mode": playback.repeat_mode,
        "queue": playback.queue,
        "updated_at": playback.updated_at.isoformat() if playback.updated_at else "",
        "song": song_data
    }


@router.get("/current", summary="Get currently active playback state")
async def get_current_player(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    playback = await PlaybackService.get_current_playback(db, current_user.id)
    return api_response(_format_playback_state(playback))


@router.post("/play", summary="Start or transfer playback of a song")
async def play_song(
    req: PlayRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        playback = await PlaybackService.play(db, current_user.id, req)
        return api_response(_format_playback_state(playback))
    except ValueError as e:
        return api_error("SONG_NOT_FOUND", str(e), status_code=404)


@router.post("/pause", summary="Pause playback")
async def pause_song(
    req: PauseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    playback = await PlaybackService.pause(db, current_user.id, req)
    return api_response(_format_playback_state(playback))


@router.post("/resume", summary="Resume playback")
async def resume_song(
    req: ResumeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    playback = await PlaybackService.resume(db, current_user.id, req)
    return api_response(_format_playback_state(playback))


@router.post("/seek", summary="Seek to specific position in song")
async def seek_song(
    req: SeekRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    playback = await PlaybackService.seek(db, current_user.id, req)
    return api_response(_format_playback_state(playback))


@router.post("/next", summary="Skip to next song in queue")
async def next_song(
    device_id: str = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    playback = await PlaybackService.next(db, current_user.id, device_id)
    return api_response(_format_playback_state(playback))


@router.post("/previous", summary="Restart song or skip to previous")
async def previous_song(
    device_id: str = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    playback = await PlaybackService.previous(db, current_user.id, device_id)
    return api_response(_format_playback_state(playback))


@router.post("/stop", summary="Stop playback completely")
async def stop_player(
    device_id: str = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    playback = await PlaybackService.stop(db, current_user.id, device_id)
    return api_response(_format_playback_state(playback))


@router.post("/sync", summary="Synchronize full playback state across devices")
async def sync_player(
    req: SyncPlaybackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    playback = await PlaybackService.sync(db, current_user.id, req)
    return api_response(_format_playback_state(playback))


@router.post("/events", summary="Ingest raw playback telemetry event")
async def ingest_event(
    req: PlaybackEventRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    event = await PlaybackService.record_event(
        db=db,
        user_id=current_user.id,
        device_id=req.device_id,
        song_id=req.song_id,
        event_type=req.event,
        position_seconds=req.position,
        duration_seconds=req.duration,
        session_id=req.session_id,
        metadata=req.metadata
    )
    return api_response({"event_id": event.id, "status": "recorded"})
