import asyncio
import json
import logging
import time
from typing import Optional, Any, Dict
import redis.asyncio as aioredis
from app.config.settings import redact_url, settings

logger = logging.getLogger("cache_service")

# Substrings that mark a rejected credential rather than a transient blip.
# Upstash answers a bad RESP password with "invalid username-password pair";
# stock Redis uses WRONGPASS / NOAUTH. None of these can succeed on retry, so
# they permanently disable Redis for the process instead of being retried on
# every cache lookup.
_AUTH_FAILURE_MARKERS = (
    "invalid username-password",
    "wrongpass",
    "noauth",
    "invalid password",
    "authentication required",
    "unknown command `auth`",
)

# After a transient failure, stop dialing Redis for this long. Without it every
# request rebuilds a client and pays the full connect timeout before falling
# back, turning an unreachable cache into latency on every endpoint.
_RETRY_COOLDOWN_SECONDS = 30


async def _aclose(client: aioredis.Redis) -> None:
    """Close a client across redis-py versions, ignoring teardown errors."""
    try:
        if hasattr(client, "aclose"):
            await client.aclose()
        else:
            await client.close()
    except Exception:
        pass


class CacheService:
    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self._memory_cache: Dict[str, tuple[Any, float]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Set once a credential is rejected; no further connection is attempted.
        self._disabled_reason: Optional[str] = None
        # Monotonic deadline before which no reconnect is attempted.
        self._retry_after: float = 0.0
        self._last_error: Optional[str] = None
        # Strong refs to in-flight teardown tasks so they are not collected.
        self._pending_closes: set = set()

    @property
    def is_connected(self) -> bool:
        """True only when a live client exists and nothing has disabled it."""
        return self.redis is not None and self._disabled_reason is None

    @property
    def disabled_reason(self) -> Optional[str]:
        """Set when Redis was permanently given up on (rejected credentials)."""
        return self._disabled_reason

    def _drop_client(self) -> None:
        """
        Discard the current client and release its connection pool.

        note_failure() is sync, so the close is scheduled on the running loop
        rather than awaited; without it a cache that fails every cooldown would
        leak a pool per failure.
        """
        stale, self.redis = self.redis, None
        self._loop = None
        if stale is None:
            return
        try:
            task = asyncio.get_running_loop().create_task(_aclose(stale))
        except RuntimeError:
            return
        self._pending_closes.add(task)
        task.add_done_callback(self._pending_closes.discard)

    def note_failure(self, exc: Exception, action: str) -> None:
        """
        Classify a Redis failure and drop the client.

        An auth failure is a configuration error: it will fail identically
        forever, so it is logged once at WARNING with the redacted URL and
        Redis is switched off for the life of the process. Anything else is
        treated as transient and retried after a cooldown, logged once per
        distinct message so a flapping cache cannot flood the log.
        """
        message = str(exc) or exc.__class__.__name__
        self._drop_client()

        if any(marker in message.lower() for marker in _AUTH_FAILURE_MARKERS):
            if self._disabled_reason is None:
                self._disabled_reason = message
                placeholder = settings.redis_url_placeholder()
                cause = (
                    f" The password is still the placeholder {placeholder!r} -- "
                    "paste the real connection string into the deployment's "
                    "environment."
                    if placeholder
                    else " The password is set but rejected: it is most likely "
                    "stale (rotated at the provider) or belongs to a different "
                    "database."
                )
                logger.warning(
                    f"Redis rejected the credentials during {action} ({message}). "
                    f"URL={redact_url(settings.get_active_redis_url())} "
                    f"from {settings.redis_url_source()}.{cause} Serving from the "
                    "in-memory cache; cross-instance playback fan-out is off "
                    "until the credentials are fixed and the service restarts."
                )
            return

        self._retry_after = time.monotonic() + _RETRY_COOLDOWN_SECONDS
        if message != self._last_error:
            logger.warning(
                f"Redis {action} failed ({message}). Falling back to the "
                f"in-memory cache; retrying in {_RETRY_COOLDOWN_SECONDS}s."
            )
            self._last_error = message
        else:
            logger.debug(f"Redis {action} still failing ({message}).")

    async def get_client(self) -> Optional[aioredis.Redis]:
        if not settings.REDIS_ENABLED or self._disabled_reason is not None:
            return None

        current_loop = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if self.redis is not None and not (current_loop and self._loop != current_loop):
            return self.redis

        if time.monotonic() < self._retry_after:
            return None

        try:
            active_url = settings.get_active_redis_url()
            self.redis = aioredis.from_url(
                active_url,
                decode_responses=True,
                socket_connect_timeout=3
            )
            self._loop = current_loop
        except Exception as e:
            self.note_failure(e, "client creation")
        return self.redis

    async def initialize(self):
        if not settings.REDIS_ENABLED:
            logger.info("Redis disabled (REDIS_ENABLED=False); using in-memory cache.")
            return
        # Named before the first connection attempt, so the log says what to fix
        # rather than only reporting the auth rejection it causes.
        placeholder = settings.redis_url_placeholder()
        if placeholder:
            logger.error(
                f"REDIS_URL still contains the placeholder {placeholder!r} "
                f"({settings.redis_url_source()}). Redis authentication will "
                "fail; replace it with the real connection string."
            )
        client = await self.get_client()
        if client:
            try:
                await client.ping()
                self._last_error = None
                self._retry_after = 0.0
                logger.info(
                    f"Connected to Redis cache at "
                    f"{redact_url(settings.get_active_redis_url())}."
                )
            except Exception as e:
                self.note_failure(e, "startup ping")

    async def close(self):
        if self.redis:
            await _aclose(self.redis)
            self.redis = None
            self._loop = None

    async def get_json(self, key: str) -> Optional[Any]:
        client = await self.get_client()
        if client:
            try:
                val = await client.get(key)
                if val:
                    return json.loads(val)
                return None
            except Exception as e:
                self.note_failure(e, "get")

        # In-memory fallback
        if key in self._memory_cache:
            val, expire_at = self._memory_cache[key]
            if expire_at == 0 or expire_at > time.time():
                return val
            else:
                del self._memory_cache[key]
        return None

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 3600):
        val_str = json.dumps(value)
        client = await self.get_client()
        if client:
            try:
                await client.set(key, val_str, ex=ttl_seconds)
                return
            except Exception as e:
                self.note_failure(e, "set")

        # In-memory fallback
        expire_at = time.time() + ttl_seconds if ttl_seconds > 0 else 0
        self._memory_cache[key] = (value, expire_at)

    async def delete(self, key: str):
        client = await self.get_client()
        if client:
            try:
                await client.delete(key)
            except Exception as e:
                self.note_failure(e, "delete")
        self._memory_cache.pop(key, None)

    async def publish(self, channel: str, message: dict):
        client = await self.get_client()
        if client:
            try:
                await client.publish(channel, json.dumps(message))
            except Exception as e:
                self.note_failure(e, "publish")


cache_service = CacheService()
