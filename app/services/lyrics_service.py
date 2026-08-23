import logging
from typing import Any, Dict, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.lyrics import Lyrics
from app.models.song import Song
from app.services.cache_service import cache_service
from app.services.catalog_service import catalog_service

logger = logging.getLogger("lyrics_service")

_CACHE_TTL_SECONDS = 7 * 24 * 3600


def _serialize(song_id: str, lyrics: Optional[Lyrics]) -> Dict[str, Any]:
    if lyrics is None:
        return {
            "song_id": song_id,
            "has_lyrics": False,
            "is_synced": False,
            "plain_text": None,
            "synced_lines": None,
            "language": None,
            "source": None,
        }
    return {
        "song_id": song_id,
        "has_lyrics": True,
        "is_synced": bool(lyrics.synced_lines),
        "plain_text": lyrics.plain_text,
        "synced_lines": lyrics.synced_lines,
        "language": lyrics.language,
        "source": lyrics.source,
    }


class LyricsService:
    async def resolve_song(self, db: AsyncSession, song_id: str) -> Optional[Song]:
        """Accepts either our UUID or a Gaana seokey, same contract as the catalog."""
        return await catalog_service.get_song_by_id(db, song_id)

    async def get_lyrics(self, db: AsyncSession, song_id: str) -> Optional[Dict[str, Any]]:
        song = await self.resolve_song(db, song_id)
        if not song:
            return None

        cache_key = f"lyrics:{song.id}"
        cached = await cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        stmt = select(Lyrics).where(Lyrics.song_id == song.id)
        res = await db.execute(stmt)
        lyrics = res.scalar_one_or_none()

        data = _serialize(song.id, lyrics)
        await cache_service.set_json(cache_key, data, ttl_seconds=_CACHE_TTL_SECONDS)
        return data

    async def upsert_lyrics(
        self,
        db: AsyncSession,
        song_id: str,
        plain_text: Optional[str],
        synced_lines: Optional[list],
        language: Optional[str],
        source: str,
    ) -> Optional[Dict[str, Any]]:
        song = await self.resolve_song(db, song_id)
        if not song:
            return None

        stmt = select(Lyrics).where(Lyrics.song_id == song.id)
        res = await db.execute(stmt)
        lyrics = res.scalar_one_or_none()

        serialized_lines = [line.model_dump() if hasattr(line, "model_dump") else line for line in (synced_lines or [])] or None

        if lyrics:
            lyrics.plain_text = plain_text
            lyrics.synced_lines = serialized_lines
            lyrics.language = language
            lyrics.source = source
        else:
            lyrics = Lyrics(
                song_id=song.id,
                plain_text=plain_text,
                synced_lines=serialized_lines,
                language=language,
                source=source,
            )
            db.add(lyrics)

        await db.commit()
        await db.refresh(lyrics)

        data = _serialize(song.id, lyrics)
        await cache_service.set_json(f"lyrics:{song.id}", data, ttl_seconds=_CACHE_TTL_SECONDS)
        return data

    async def delete_lyrics(self, db: AsyncSession, song_id: str) -> bool:
        song = await self.resolve_song(db, song_id)
        if not song:
            return False

        stmt = select(Lyrics).where(Lyrics.song_id == song.id)
        res = await db.execute(stmt)
        lyrics = res.scalar_one_or_none()
        if not lyrics:
            return False

        await db.delete(lyrics)
        await db.commit()
        await cache_service.delete(f"lyrics:{song.id}")
        return True


lyrics_service = LyricsService()
