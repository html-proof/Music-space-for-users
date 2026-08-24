import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.config.settings import settings
from app.db.base import is_uuid
from app.models.playback import CurrentPlayback, PlaybackEvent
from app.models.device import Device
from app.models.history import ListeningHistory
from app.models.song import Song
from app.schemas.playback import PlayRequest, PauseRequest, ResumeRequest, SeekRequest, SyncPlaybackRequest, PlaybackEventRequest, RadioStartRequest
from app.services.cache_service import cache_service
from app.models.catalog_sync import PRIORITY_NEXT, PRIORITY_PLAYING
from app.services.catalog_service import catalog_service
from app.services.catalog_sync_service import catalog_sync_service
from app.services.history_service import history_service
from app.services.radio_service import RadioService, DEFAULT_BATCH_SIZE
from app.utils.cache_keys import playback_state_key, player_channel
from app.websocket.connection_manager import manager
from app.websocket.pubsub import INSTANCE_ID

logger = logging.getLogger("playback_service")

# Restarting counts as "previous" only within this many seconds of a track's
# start; past it, "previous" restarts the current track instead.
PREVIOUS_RESTART_THRESHOLD_SECONDS = 3.0


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
    async def _broadcast_playback_update(
        user_id: str,
        playback: CurrentPlayback,
        exclude_device_id: Optional[str] = None
    ):
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
        await cache_service.set_json(playback_state_key(user_id), state_data, ttl_seconds=86400)

        # Deliver to this instance's own sockets first -- without this, REST-driven
        # state changes never reached any connected device.
        try:
            await manager.broadcast_to_user(
                user_id=user_id,
                message=state_data,
                exclude_device_id=exclude_device_id
            )
        except Exception as e:
            logger.error(f"Local playback broadcast failed for user {user_id}: {e}")

        # Then fan out to any other instances holding sockets for this user.
        # Tagged so the listener in this process skips what was just delivered.
        await cache_service.publish(
            player_channel(user_id),
            {**state_data, "origin_instance": INSTANCE_ID}
        )

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

        # An explicit play normally establishes a new listening context, so any
        # active radio station is dropped -- otherwise a station could resurrect
        # itself later (inside its TTL) once some unrelated queue ran out.
        # Playing a track that is already part of the current station is a client
        # following the station rather than leaving it, so that keeps it.
        station_context = set(playback.queue or [])
        if playback.song_id:
            station_context.add(playback.song_id)
        leaving_station = req.queue is not None or song.id not in station_context

        # Update song play count
        song.play_count += 1

        playback.song_id = song.id
        # playlist_id is optional context with no resolver; keep it only when it
        # is a real uuid so it can be bound to the uuid column.
        playback.playlist_id = req.playlist_id if is_uuid(req.playlist_id) else None
        playback.device_id = req.device_id or playback.device_id
        playback.position_seconds = req.position_seconds
        playback.duration_seconds = float(song.duration)
        playback.state = "playing"
        if req.queue is not None:
            playback.queue = req.queue

        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)

        # What is playing now, and what plays next, jump the sync queue: those
        # two are the only tracks whose data the user is about to need, and
        # they would otherwise wait behind however much background catalog fill
        # is already queued.
        upcoming = list(playback.queue or [])[:1]
        await catalog_sync_service.prioritize_songs(
            db,
            [(song.id, PRIORITY_PLAYING)] + [(sid, PRIORITY_NEXT) for sid in upcoming],
        )

        if leaving_station:
            await RadioService.clear_station(user_id)

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
    async def start_radio(db: AsyncSession, user_id: str, req: RadioStartRequest) -> CurrentPlayback:
        """
        Begin an endless station. Raises LookupError when the seed cannot be
        resolved, and ValueError when there is nothing at all to play.
        """
        seed_id = await RadioService.resolve_seed(db, req.seed_type, req.seed_id)
        if req.seed_type != "personalized" and seed_id is None:
            raise LookupError(f"No {req.seed_type} matching {req.seed_id!r} was found.")

        batch = await RadioService.build_batch(
            db,
            user_id=user_id,
            seed_type=req.seed_type,
            seed_id=seed_id,
            limit=DEFAULT_BATCH_SIZE,
            allow_network=True
        )
        if not batch:
            raise ValueError("No tracks are available to build a station from.")

        playback = await PlaybackService.get_current_playback(db, user_id)
        first = batch[0]
        first.play_count += 1

        playback.song_id = first.id
        # A station is not a playlist; drop any stale playlist context.
        playback.playlist_id = None
        playback.device_id = req.device_id or playback.device_id
        playback.position_seconds = 0.0
        playback.duration_seconds = float(first.duration)
        playback.state = "playing"
        playback.queue = [s.id for s in batch[1:]]
        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)

        await RadioService.set_station(
            user_id,
            seed_type=req.seed_type,
            seed_id=seed_id,
            served=[s.id for s in batch]
        )

        await PlaybackService.record_event(
            db=db,
            user_id=user_id,
            device_id=req.device_id or playback.device_id or "default",
            song_id=first.id,
            event_type="PLAY",
            position_seconds=0.0,
            duration_seconds=float(first.duration)
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
    def _was_skipped(position_seconds: float, duration_seconds: float) -> bool:
        """
        A track counts as skipped only when the listener left early. Recording a
        near-complete play as a skip poisons the recommendation signal, which
        weights skips negatively.
        """
        completion = (position_seconds / max(duration_seconds, 1.0)) * 100
        return (
            completion < float(settings.MIN_COMPLETION_PERCENTAGE)
            and position_seconds < float(settings.MIN_LISTEN_SECONDS)
        )

    @staticmethod
    async def _autoplay_refill(
        db: AsyncSession,
        user_id: str,
        playback: CurrentPlayback
    ) -> List[Song]:
        """
        Tracks to continue with once the queue runs dry.

        Uses the active station when there is one; otherwise seeds an implicit
        station from whatever was playing, which is what makes a session keep
        going instead of dead-ending. Local-only, so a skip never waits on a
        network round trip. Returns [] when there is nothing to continue with,
        and the caller then stops.
        """
        station = await RadioService.get_station(user_id)
        if station:
            seed_type = station.get("seed_type") or "personalized"
            seed_id = station.get("seed_id")
            served = [str(s) for s in (station.get("served") or []) if s]
        elif playback.song_id:
            seed_type, seed_id, served = "song", playback.song_id, [playback.song_id]
        else:
            return []

        exclude = set(served)
        if playback.song_id:
            exclude.add(playback.song_id)

        batch = await RadioService.build_batch(
            db,
            user_id=user_id,
            seed_type=seed_type,
            seed_id=seed_id,
            exclude_ids=exclude,
            limit=DEFAULT_BATCH_SIZE,
            allow_network=False
        )
        if not batch:
            return []

        # Persist the (possibly newly implicit) station so the next exhaustion
        # continues from here rather than repeating these same tracks.
        await RadioService.set_station(
            user_id,
            seed_type=seed_type,
            seed_id=seed_id,
            served=served + [s.id for s in batch]
        )
        return batch

    @staticmethod
    async def next(db: AsyncSession, user_id: str, device_id: Optional[str] = None) -> CurrentPlayback:
        playback = await PlaybackService.get_current_playback(db, user_id)
        # Record the outgoing song. Whether this is a skip depends on how much
        # of it was actually heard, not on the fact that `next` was pressed.
        if playback.song_id:
            completion = (playback.position_seconds / max(playback.duration_seconds, 1.0)) * 100
            skipped = PlaybackService._was_skipped(playback.position_seconds, playback.duration_seconds)
            await history_service.record_history(
                db=db,
                user_id=user_id,
                song_id=playback.song_id,
                device_id=device_id or playback.device_id,
                duration_listened=playback.position_seconds,
                completion_percentage=min(completion, 100.0),
                skipped=skipped,
                source="queue"
            )
            await PlaybackService.record_event(
                db=db,
                user_id=user_id,
                device_id=device_id or playback.device_id or "default",
                song_id=playback.song_id,
                event_type="SKIP" if skipped else "NEXT",
                position_seconds=playback.position_seconds,
                duration_seconds=playback.duration_seconds
            )

        # Advance queue. Reassign the list rather than mutating in place so the
        # JSON column is flagged dirty and the change is actually persisted.
        queue = list(playback.queue or [])
        if queue:
            next_song_id = queue.pop(0)
            next_song = await catalog_service.get_song_by_id(db, next_song_id)
            if next_song:
                playback.song_id = next_song.id
                playback.duration_seconds = float(next_song.duration)
                playback.position_seconds = 0.0
                playback.state = "playing"
                next_song.play_count += 1
            playback.queue = queue
        elif playback.repeat_mode == "one" and playback.song_id:
            playback.position_seconds = 0.0
            playback.state = "playing"
        else:
            # Queue exhausted: autoplay keeps the station going. Only when there
            # is genuinely nothing left to play does playback stop.
            refill = await PlaybackService._autoplay_refill(db, user_id, playback)
            if refill:
                next_song = refill[0]
                next_song.play_count += 1
                playback.song_id = next_song.id
                playback.duration_seconds = float(next_song.duration)
                playback.position_seconds = 0.0
                playback.state = "playing"
                playback.queue = [s.id for s in refill[1:]]
            else:
                playback.position_seconds = 0.0
                playback.state = "stopped"

        if device_id:
            playback.device_id = device_id
        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)
        await PlaybackService._broadcast_playback_update(user_id, playback)
        return playback

    @staticmethod
    async def _previous_song_id(db: AsyncSession, user_id: str, current_song_id: Optional[str]) -> Optional[str]:
        """Most recently played song that is not the one playing now."""
        stmt = (
            select(ListeningHistory.song_id)
            .where(ListeningHistory.user_id == user_id)
            .order_by(desc(ListeningHistory.started_at))
            .limit(20)
        )
        res = await db.execute(stmt)
        for song_id in res.scalars().all():
            if song_id and song_id != current_song_id:
                return song_id
        return None

    @staticmethod
    async def previous(db: AsyncSession, user_id: str, device_id: Optional[str] = None) -> CurrentPlayback:
        playback = await PlaybackService.get_current_playback(db, user_id)

        # Past the restart threshold, "previous" restarts the current track --
        # the conventional player behaviour. Only near the start does it step
        # back to the previously played song.
        stepped_back = False
        if playback.position_seconds <= PREVIOUS_RESTART_THRESHOLD_SECONDS:
            prev_song_id = await PlaybackService._previous_song_id(db, user_id, playback.song_id)
            if prev_song_id:
                prev_song = await catalog_service.get_song_by_id(db, prev_song_id)
                if prev_song:
                    # Push the current track back onto the front of the queue so
                    # going forward again returns to it.
                    if playback.song_id:
                        playback.queue = [playback.song_id] + list(playback.queue or [])
                    playback.song_id = prev_song.id
                    playback.duration_seconds = float(prev_song.duration)
                    prev_song.play_count += 1
                    stepped_back = True

        playback.position_seconds = 0.0
        playback.state = "playing" if playback.song_id else "stopped"
        if device_id:
            playback.device_id = device_id
        playback.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(playback)

        if playback.song_id:
            await PlaybackService.record_event(
                db=db,
                user_id=user_id,
                device_id=device_id or playback.device_id or "default",
                song_id=playback.song_id,
                event_type="PREVIOUS" if stepped_back else "SEEK",
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
                skipped=PlaybackService._was_skipped(playback.position_seconds, playback.duration_seconds),
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
        # Stop has to genuinely stop: without this the station would still be
        # live and the next exhausted queue would silently start playing again.
        await RadioService.clear_station(user_id)
        await PlaybackService._broadcast_playback_update(user_id, playback)
        return playback

    @staticmethod
    async def sync(db: AsyncSession, user_id: str, req: SyncPlaybackRequest) -> CurrentPlayback:
        playback = await PlaybackService.get_current_playback(db, user_id)
        playback.device_id = req.device_id
        # song_id/playlist_id come from the client's reported state and land on
        # uuid columns (song_id is an FK to songs). Resolve the song the same
        # way play/events do so a non-uuid or unknown id becomes "no song"
        # rather than a bind-time or foreign-key 500.
        if req.song_id:
            song = await catalog_service.get_song_by_id(db, req.song_id)
            playback.song_id = song.id if song else None
        if req.playlist_id:
            playback.playlist_id = req.playlist_id if is_uuid(req.playlist_id) else None
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
