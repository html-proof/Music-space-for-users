import asyncio
import json
import logging
import uuid
from typing import Optional

from app.config.settings import settings
from app.services.cache_service import cache_service
from app.websocket.connection_manager import manager

logger = logging.getLogger("player_pubsub")

# Channels published to by PlaybackService._broadcast_playback_update.
PLAYER_CHANNEL_PATTERN = "user:*:player"

# Identifies this process so it can ignore its own published messages and avoid
# double-delivering to sockets it already wrote to directly.
INSTANCE_ID = str(uuid.uuid4())


class PlayerPubSub:
    """
    Bridges Redis pub/sub to local WebSocket connections.

    PlaybackService publishes every state change to `user:<id>:player`. A single
    process delivers those to its own sockets directly; this listener is what
    makes the fan-out work when more than one instance is running, since each
    instance only holds the sockets for the devices connected to it.
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._pubsub = None
        self._stopping = False

    async def start(self) -> bool:
        if not settings.REDIS_ENABLED:
            logger.info("Redis disabled; cross-instance playback fan-out inactive.")
            return False
        if self._task and not self._task.done():
            return True

        client = await cache_service.get_client()
        if client is None:
            logger.warning("No Redis client; cross-instance playback fan-out inactive.")
            return False

        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="player-pubsub")
        return True

    async def _run(self) -> None:
        while not self._stopping:
            try:
                client = await cache_service.get_client()
                if client is None:
                    await asyncio.sleep(5)
                    continue

                self._pubsub = client.pubsub()
                await self._pubsub.psubscribe(PLAYER_CHANNEL_PATTERN)
                logger.info(f"Subscribed to {PLAYER_CHANNEL_PATTERN} for playback fan-out.")

                async for raw in self._pubsub.listen():
                    if self._stopping:
                        break
                    if raw.get("type") != "pmessage":
                        continue
                    await self._dispatch(raw)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._stopping:
                    break
                logger.warning(f"Playback pub/sub listener error ({e}); reconnecting in 5s.")
                await asyncio.sleep(5)
            finally:
                await self._close_pubsub()

    async def _dispatch(self, raw: dict) -> None:
        try:
            data = raw.get("data")
            message = json.loads(data) if isinstance(data, (str, bytes)) else data
            if not isinstance(message, dict):
                return
            user_id = message.get("user_id")
            if not user_id:
                # Fall back to parsing the channel: user:<id>:player
                channel = raw.get("channel") or ""
                parts = str(channel).split(":")
                user_id = parts[1] if len(parts) >= 3 else None
            if not user_id:
                return
            if message.get("origin_instance") == INSTANCE_ID:
                # Already delivered locally by the publishing process.
                return
            await manager.broadcast_to_user(user_id=user_id, message=message)
        except Exception as e:
            logger.error(f"Failed to dispatch playback pub/sub message: {e}")

    async def _close_pubsub(self) -> None:
        if self._pubsub is not None:
            try:
                await self._pubsub.punsubscribe(PLAYER_CHANNEL_PATTERN)
            except Exception:
                pass
            try:
                await self._pubsub.aclose()
            except Exception:
                try:
                    await self._pubsub.close()
                except Exception:
                    pass
            self._pubsub = None

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self._close_pubsub()


player_pubsub = PlayerPubSub()
