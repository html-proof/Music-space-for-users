import asyncio
import json
import logging
import time
from typing import Optional, Any, Dict
import redis.asyncio as aioredis
from app.config.settings import settings

logger = logging.getLogger("cache_service")


class CacheService:
    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self._memory_cache: Dict[str, tuple[Any, float]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def get_client(self) -> Optional[aioredis.Redis]:
        if not settings.REDIS_ENABLED:
            return None

        current_loop = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if self.redis is None or (current_loop and self._loop != current_loop):
            try:
                active_url = settings.get_active_redis_url()
                self.redis = aioredis.from_url(
                    active_url,
                    decode_responses=True,
                    socket_connect_timeout=3
                )
                self._loop = current_loop
            except Exception as e:
                logger.warning(f"Failed to create Redis client ({e}).")
                self.redis = None
        return self.redis

    async def initialize(self):
        client = await self.get_client()
        if client:
            try:
                await client.ping()
                logger.info("Connected to Redis cache.")
            except Exception as e:
                logger.warning(f"Redis ping failed ({e}). Falling back to in-memory cache.")
                self.redis = None

    async def close(self):
        if self.redis:
            try:
                if hasattr(self.redis, "aclose"):
                    await self.redis.aclose()
                else:
                    await self.redis.close()
            except Exception:
                pass
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
                logger.error(f"Redis get error: {e}")

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
                logger.error(f"Redis set error: {e}")

        # In-memory fallback
        expire_at = time.time() + ttl_seconds if ttl_seconds > 0 else 0
        self._memory_cache[key] = (value, expire_at)

    async def delete(self, key: str):
        client = await self.get_client()
        if client:
            try:
                await client.delete(key)
            except Exception as e:
                logger.error(f"Redis delete error: {e}")
        self._memory_cache.pop(key, None)

    async def publish(self, channel: str, message: dict):
        client = await self.get_client()
        if client:
            try:
                await client.publish(channel, json.dumps(message))
            except Exception as e:
                logger.error(f"Redis publish error: {e}")


cache_service = CacheService()
