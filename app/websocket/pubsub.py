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

    # Reconnect backoff grows 5s, 10s, 20s ... capped, so a permanent failure
    # (e.g. a bad REDIS_URL) settles into rare retries instead of hammering
    # every 5s. The exponent is clamped to keep the shift cheap.
    _BASE_BACKOFF_SECONDS = 5
    _MAX_BACKOFF_SECONDS = 300

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._pubsub = None
        self._stopping = False

    @classmethod
    def _backoff(cls, failures: int) -> int:
        exp = min(failures, 10)
        return min(cls._BASE_BACKOFF_SECONDS * (2 ** exp), cls._MAX_BACKOFF_SECONDS)

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
        # An auth/config failure is permanent, not a transient blip: retrying
        # every 5s and logging each time floods the log forever. Only the first
        # sighting of an error (or a changed one) is a WARNING; the same error
        # repeating is demoted to DEBUG. A successful subscribe resets both, so
        # genuine transient drops stay visible.
        failures = 0
        last_error: Optional[str] = None
        while not self._stopping:
            try:
                client = await cache_service.get_client()
                if client is None:
                    if cache_service.disabled_reason:
                        logger.warning(
                            "Redis credentials were rejected; stopping playback "
                            "fan-out listener. Single-instance playback still "
                            "works, but state will not sync across instances."
                        )
                        return
                    await asyncio.sleep(self._backoff(failures))
                    failures += 1
                    continue

                self._pubsub = client.pubsub()
                await self._pubsub.psubscribe(PLAYER_CHANNEL_PATTERN)
                logger.info(f"Subscribed to {PLAYER_CHANNEL_PATTERN} for playback fan-out.")
                failures = 0
                last_error = None

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
                # Let the cache service classify it: a rejected credential is a
                # config error it latches on, and no amount of reconnecting can
                # clear it, so the listener stops instead of retrying forever.
                cache_service.note_failure(e, "pub/sub subscribe")
                if cache_service.disabled_reason:
                    logger.warning(
                        "Stopping playback fan-out listener. Single-instance "
                        "playback still works, but state will not sync across "
                        "instances until the Redis credentials are fixed."
                    )
                    return
                delay = self._backoff(failures)
                message = str(e)
                if message != last_error:
                    logger.warning(
                        f"Playback pub/sub listener error ({message}); "
                        f"reconnecting in {delay}s."
                    )
                    last_error = message
                else:
                    logger.debug(
                        f"Playback pub/sub still failing ({message}); "
                        f"retrying in {delay}s."
                    )
                failures += 1
                await asyncio.sleep(delay)
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
