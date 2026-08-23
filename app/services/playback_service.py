import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.playback import CurrentPlayback, PlaybackEvent
from app.models.device import Device
from app.models.song import Song
from app.schemas.playback import PlayRequest, PauseRequest, ResumeRequest, SeekRequest, SyncPlaybackRequest, PlaybackEventRequest
from app.services.cache_service import cache_service
from app.services.catalog_service import catalog_service
from app.services.history_service import history_service

logger = logging.getLogger("playback_service")


class PlaybackService:
    @staticmethod
    async def get_current_playback(db: AsyncSession, user_id: str) -> CurrentPlayback:
        stmt = select(CurrentPlayback).where(CurrentPlayback.user_id == user_id)
        res = await db.execute(stmt)
        playback = res.scalar_one_or_none()
        if not playback:
            playback = CurrentPlayback(
                user_id=user_id,
                state="stopped",
                position_seconds=0.0,
                duration_seconds=0.0,
                volume=100,
                shuffle=False,
                repeat_mode="off",
                queue=[]
            )
            db.add(playback)
            await db.commit()
            await db.refresh(playback)
        return playback

    @staticmethod
    async def _broadcast_playback_update(user_id: str, playback: CurrentPlayback):
        state_data = {
            "type": "PLAYBACK_UPDATED",
            "user_id": user_id,
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
            "updated_at": playback.updated_at.isoformat() if playback.updated_at else datetime.now(timezone.utc).isoformat()
        }
        await cache_service.set_json(f"playback:user:{user_id}", state_data, ttl_seconds=86400)
        await cache_service.publish(f"user:{user_id}:player", state_data)

    @staticmethod
    async def record_event(
        db: AsyncSession,
        user_id: str,
        device_id: str,
        song_id: str,
        event_type: str,
        position_seconds: float = 0.0,
        duration_seconds: float = 0.0,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> PlaybackEvent:
        event = PlaybackEvent(
            user_id=user_id,
            device_id=device_id or "unknown",
            song_id=song_id,
            event_type=event_type.upper(),
            position_seconds=position_seconds,
            duration_seconds=duration_seconds,
            session_id=session_id,
            event_metadata=metadata or {}
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)
        return event

    @staticmethod
    async def play(db: AsyncSession, user_id: str, req: PlayRequest) -> CurrentPlayback:
        playback = await PlaybackService.get_current_playback(db, user_id)
        song = await catalog_service.get_song_by_id(db, req.song_id)
        if not song:
            raise ValueError(f"Song {req.song_id} not found.")

        # Update song play count
        song.play_count += 1

        playback.song_id = song.id
        playback.playlist_id = req.playlist_id
        playback.device_id = req.device_id or playback.device_id
        playback.position_seconds = req.position_seconds
        playback.duration_seconds = float(song.duration)
        playback.state = "playing"
        if req.queue is not None:
            playback.queue = req.queue

        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)

        # Log event
        await PlaybackService.record_event(
            db=db,
            user_id=user_id,
            device_id=req.device_id or "default",
            song_id=song.id,
            event_type="PLAY",
            position_seconds=req.position_seconds,
            duration_seconds=float(song.duration)
        )

        await PlaybackService._broadcast_playback_update(user_id, playback)
        return playback

    @staticmethod
    async def pause(db: AsyncSession, user_id: str, req: PauseRequest) -> CurrentPlayback:
        playback = await PlaybackService.get_current_playback(db, user_id)
        playback.state = "paused"
        playback.position_seconds = req.position_seconds or playback.position_seconds
        if req.device_id:
            playback.device_id = req.device_id
        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)

        if playback.song_id:
            await PlaybackService.record_event(
                db=db,
                user_id=user_id,
                device_id=req.device_id or playback.device_id or "default",
                song_id=playback.song_id,
                event_type="PAUSE",
                position_seconds=playback.position_seconds,
                duration_seconds=playback.duration_seconds
            )

        await PlaybackService._broadcast_playback_update(user_id, playback)
        return playback

    @staticmethod
    async def resume(db: AsyncSession, user_id: str, req: ResumeRequest) -> CurrentPlayback:
        playback = await PlaybackService.get_current_playback(db, user_id)
        playback.state = "playing"
        if req.position_seconds is not None:
            playback.position_seconds = req.position_seconds
        if req.device_id:
            playback.device_id = req.device_id
        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)

        if playback.song_id:
            await PlaybackService.record_event(
                db=db,
                user_id=user_id,
                device_id=req.device_id or playback.device_id or "default",
                song_id=playback.song_id,
                event_type="RESUME",
                position_seconds=playback.position_seconds,
                duration_seconds=playback.duration_seconds
            )

        await PlaybackService._broadcast_playback_update(user_id, playback)
        return playback

    @staticmethod
    async def seek(db: AsyncSession, user_id: str, req: SeekRequest) -> CurrentPlayback:
        playback = await PlaybackService.get_current_playback(db, user_id)
        playback.position_seconds = req.position_seconds
        if req.device_id:
            playback.device_id = req.device_id
        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)

        if playback.song_id:
            await PlaybackService.record_event(
                db=db,
                user_id=user_id,
                device_id=req.device_id or playback.device_id or "default",
                song_id=playback.song_id,
                event_type="SEEK",
                position_seconds=playback.position_seconds,
                duration_seconds=playback.duration_seconds
            )

        await PlaybackService._broadcast_playback_update(user_id, playback)
        return playback

    @staticmethod
    async def next(db: AsyncSession, user_id: str, device_id: Optional[str] = None) -> CurrentPlayback:
        playback = await PlaybackService.get_current_playback(db, user_id)
        # Record skip/next on current song
        if playback.song_id:
            completion = (playback.position_seconds / max(playback.duration_seconds, 1.0)) * 100
            await history_service.record_history(
                db=db,
                user_id=user_id,
                song_id=playback.song_id,
                device_id=device_id or playback.device_id,
                duration_listened=playback.position_seconds,
                completion_percentage=min(completion, 100.0),
                skipped=True,
                source="queue"
            )
            await PlaybackService.record_event(
                db=db,
                user_id=user_id,
                device_id=device_id or playback.device_id or "default",
                song_id=playback.song_id,
                event_type="SKIP",
                position_seconds=playback.position_seconds,
                duration_seconds=playback.duration_seconds
            )

        # Advance queue
        if playback.queue and len(playback.queue) > 0:
            next_song_id = playback.queue.pop(0)
            next_song = await catalog_service.get_song_by_id(db, next_song_id)
            if next_song:
                playback.song_id = next_song.id
                playback.duration_seconds = float(next_song.duration)
                playback.position_seconds = 0.0
                playback.state = "playing"
                next_song.play_count += 1

        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)
        await PlaybackService._broadcast_playback_update(user_id, playback)
        return playback

    @staticmethod
    async def previous(db: AsyncSession, user_id: str, device_id: Optional[str] = None) -> CurrentPlayback:
        playback = await PlaybackService.get_current_playback(db, user_id)
        playback.position_seconds = 0.0
        playback.state = "playing"
        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)

        if playback.song_id:
            await PlaybackService.record_event(
                db=db,
                user_id=user_id,
                device_id=device_id or playback.device_id or "default",
                song_id=playback.song_id,
                event_type="PREVIOUS",
                position_seconds=0.0,
                duration_seconds=playback.duration_seconds
            )

        await PlaybackService._broadcast_playback_update(user_id, playback)
        return playback

    @staticmethod
    async def stop(db: AsyncSession, user_id: str, device_id: Optional[str] = None) -> CurrentPlayback:
        playback = await PlaybackService.get_current_playback(db, user_id)
        if playback.song_id:
            completion = (playback.position_seconds / max(playback.duration_seconds, 1.0)) * 100
            await history_service.record_history(
                db=db,
                user_id=user_id,
                song_id=playback.song_id,
                device_id=device_id or playback.device_id,
                duration_listened=playback.position_seconds,
                completion_percentage=min(completion, 100.0),
                skipped=(completion < 50.0 and playback.position_seconds < 30.0),
                source="player"
            )
            await PlaybackService.record_event(
                db=db,
                user_id=user_id,
                device_id=device_id or playback.device_id or "default",
                song_id=playback.song_id,
                event_type="STOP",
                position_seconds=playback.position_seconds,
                duration_seconds=playback.duration_seconds
            )

        playback.state = "stopped"
        playback.position_seconds = 0.0
        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)
        await PlaybackService._broadcast_playback_update(user_id, playback)
        return playback

    @staticmethod
    async def sync(db: AsyncSession, user_id: str, req: SyncPlaybackRequest) -> CurrentPlayback:
        playback = await PlaybackService.get_current_playback(db, user_id)
        playback.device_id = req.device_id
        if req.song_id:
            playback.song_id = req.song_id
        if req.playlist_id:
            playback.playlist_id = req.playlist_id
        playback.position_seconds = req.position_seconds
        playback.duration_seconds = req.duration_seconds
        playback.state = req.state
        playback.volume = req.volume
        playback.shuffle = req.shuffle
        playback.repeat_mode = req.repeat_mode
        if req.queue is not None:
            playback.queue = req.queue

        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)
        await PlaybackService._broadcast_playback_update(user_id, playback)
        return playback


playback_service = PlaybackService()
