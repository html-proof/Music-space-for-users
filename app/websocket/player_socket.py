import json
import logging
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.websocket.connection_manager import manager
from app.config.firebase import verify_firebase_token
from app.db.database import async_session_factory
from app.services.auth_service import AuthService
from app.services.playback_service import PlaybackService
from app.schemas.playback import PlayRequest, PauseRequest, SeekRequest

logger = logging.getLogger("player_socket")
ws_router = APIRouter()


@ws_router.websocket("/ws/player/{device_id}")
async def player_websocket_endpoint(
    websocket: WebSocket,
    device_id: str,
    token: Optional[str] = Query(None)
):
    # Verify auth
    user_id = None
    if token:
        try:
            payload = verify_firebase_token(token)
            async with async_session_factory() as db:
                user = await AuthService.get_user_by_firebase_uid(db, payload["uid"])
                if not user:
                    user = await AuthService.sync_user(db, payload)
                user_id = user.id
        except Exception as e:
            logger.warning(f"WebSocket auth failed via query param: {e}")

    if not user_id:
        await websocket.accept()
        # Expect first message to be auth
        try:
            init_msg = await websocket.receive_text()
            data = json.loads(init_msg)
            if data.get("type") == "AUTH" and data.get("token"):
                payload = verify_firebase_token(data["token"])
                async with async_session_factory() as db:
                    user = await AuthService.get_user_by_firebase_uid(db, payload["uid"])
                    if not user:
                        user = await AuthService.sync_user(db, payload)
                    user_id = user.id
            else:
                await websocket.send_text(json.dumps({"type": "ERROR", "message": "Authentication required"}))
                await websocket.close(code=1008)
                return
        except Exception as e:
            logger.error(f"Initial websocket auth message failed: {e}")
            await websocket.close(code=1008)
            return

    await manager.connect(websocket, user_id=user_id, device_id=device_id)

    # Send initial playback state
    try:
        async with async_session_factory() as db:
            current = await PlaybackService.get_current_playback(db, user_id)
            await websocket.send_text(json.dumps({
                "type": "PLAYBACK_STATE",
                "state": current.state,
                "song_id": current.song_id,
                "playlist_id": current.playlist_id,
                "position_seconds": current.position_seconds,
                "duration_seconds": current.duration_seconds,
                "volume": current.volume,
                "shuffle": current.shuffle,
                "repeat_mode": current.repeat_mode,
                "queue": current.queue,
            }))
    except Exception as e:
        logger.error(f"Failed to send initial playback state: {e}")

    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                msg = json.loads(raw_data)
                msg_type = msg.get("type", "").upper()

                async with async_session_factory() as db:
                    if msg_type == "PLAY":
                        song_id = msg.get("song_id")
                        if song_id:
                            play_req = PlayRequest(
                                song_id=song_id,
                                device_id=device_id,
                                position_seconds=msg.get("position", 0.0),
                                queue=msg.get("queue")
                            )
                            await PlaybackService.play(db, user_id, play_req)

                    elif msg_type == "PAUSE":
                        pause_req = PauseRequest(
                            device_id=device_id,
                            position_seconds=msg.get("position", 0.0)
                        )
                        await PlaybackService.pause(db, user_id, pause_req)

                    elif msg_type == "SEEK":
                        seek_req = SeekRequest(
                            device_id=device_id,
                            position_seconds=msg.get("position", 0.0)
                        )
                        await PlaybackService.seek(db, user_id, seek_req)

                    elif msg_type == "NEXT":
                        await PlaybackService.next(db, user_id, device_id=device_id)

                    elif msg_type == "PREVIOUS":
                        await PlaybackService.previous(db, user_id, device_id=device_id)

                    elif msg_type == "STOP":
                        await PlaybackService.stop(db, user_id, device_id=device_id)

                    elif msg_type == "PING":
                        await websocket.send_text(json.dumps({"type": "PONG"}))

            except Exception as e:
                logger.error(f"Error handling websocket command: {e}")
                await websocket.send_text(json.dumps({"type": "ERROR", "message": str(e)}))

    except WebSocketDisconnect:
        manager.disconnect(user_id=user_id, device_id=device_id)
        await manager.broadcast_to_user(
            user_id=user_id,
            message={"type": "DEVICE_DISCONNECTED", "device_id": device_id}
        )
