import asyncio
import logging
from typing import List, Optional, Dict, Any
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from api.gaanapy import GaanaPy
from app.db.base import is_uuid
from app.models.song import Song, Artist, Album
from app.services.cache_service import cache_service

logger = logging.getLogger("catalog_service")

# GaanaPy's own aiohttp session has a 30s timeout, which is fine for a
# foreground search but too generous for a home-feed shelf that must still
# leave room for everything else in the same request (upserts, other
# shelves) inside the client's 45s budget. Bounding just the raw network
# call -- never the DB upserts that follow it -- means a slow/unresponsive
# Gaana falls back to whatever is already cached in Postgres quickly instead
# of stalling the whole response.
TRENDING_FETCH_TIMEOUT_SECONDS = 8.0


class CatalogService:
    def __init__(self):
        self.gaana = GaanaPy()

    async def close(self):
        if hasattr(self.gaana, '_aiohttp') and self.gaana._aiohttp and not self.gaana._aiohttp.closed:
            await self.gaana._aiohttp.close()

    async def get_or_create_artist(self, db: AsyncSession, name: str, seokey: Optional[str] = None, external_id: Optional[str] = None, image_url: Optional[str] = None) -> Artist:
        ext_id = external_id or seokey or name.lower().replace(" ", "-")
        stmt = select(Artist).where(or_(Artist.external_id == ext_id, Artist.name == name))
        res = await db.execute(stmt)
        artist = res.scalars().first()
        if not artist:
            artist = Artist(
                external_id=ext_id,
                name=name,
                seokey=seokey,
                image_url=image_url
            )
            db.add(artist)
            await db.flush()
        return artist

    async def get_or_create_album(self, db: AsyncSession, title: str, seokey: Optional[str] = None, external_id: Optional[str] = None, cover_url: Optional[str] = None, artist_id: Optional[str] = None, artist_name: str = "") -> Album:
        ext_id = external_id or seokey or title.lower().replace(" ", "-")
        stmt = select(Album).where(or_(Album.external_id == ext_id, Album.title == title))
        res = await db.execute(stmt)
        album = res.scalars().first()
        if not album:
            album = Album(
                external_id=ext_id,
                title=title,
                seokey=seokey,
                cover_url=cover_url,
                artist_id=artist_id,
                artist_name=artist_name
            )
            db.add(album)
            await db.flush()
        return album

    async def upsert_gaana_song(self, db: AsyncSession, raw_song: Dict[str, Any]) -> Song:
        seokey = raw_song.get("seokey") or raw_song.get("track_id") or "unknown-track"
        title = raw_song.get("title") or raw_song.get("track_title") or "Unknown Title"
        artist_name = raw_song.get("artists") or raw_song.get("artist") or "Unknown Artist"
        album_name = raw_song.get("album") or raw_song.get("album_title") or "Single"
        
        # Duration parsing
        try:
            duration = int(raw_song.get("duration", 0))
        except (ValueError, TypeError):
            duration = 0

        # Images & Streams
        images = raw_song.get("images", {}).get("urls", {})
        thumbnail_url = images.get("large_artwork") or images.get("medium_artwork") or raw_song.get("artwork_large", "")
        stream_urls = raw_song.get("stream_urls", {}).get("urls", {})
        audio_url = stream_urls.get("very_high_quality") or stream_urls.get("high_quality") or stream_urls.get("medium_quality") or ""

        # Check existing
        stmt = select(Song).where(Song.external_id == seokey)
        res = await db.execute(stmt)
        song = res.scalar_one_or_none()

        artist = await self.get_or_create_artist(
            db,
            name=artist_name.split(",")[0].strip() if artist_name else "Unknown Artist",
            seokey=raw_song.get("artist_seokeys", "").split(",")[0].strip() if raw_song.get("artist_seokeys") else None,
            external_id=raw_song.get("artist_ids", "").split(",")[0].strip() if raw_song.get("artist_ids") else None,
            image_url=raw_song.get("artist_image")
        )

        album_seokey = raw_song.get("album_seokey")
        album_ext_id = raw_song.get("album_id") or album_seokey
        if not album_ext_id:
            # No real album identifier from Gaana (common for singles), and
            # get_or_create_album falls back to matching by title when there
            # is none -- every such track shares the literal title "Single",
            # which would otherwise merge unrelated singles from every artist
            # into one shared "Single" album. Keying off this song's own
            # seokey instead keeps each single its own album, one row per
            # song rather than one row for the whole catalog.
            album_ext_id = f"single-{seokey}"

        album = await self.get_or_create_album(
            db,
            title=album_name,
            seokey=album_seokey,
            external_id=album_ext_id,
            cover_url=thumbnail_url,
            artist_id=artist.id,
            artist_name=artist.name
        )

        if song:
            song.title = title
            song.artist_id = artist.id
            song.artist_name = artist_name
            song.album_id = album.id
            song.album_name = album_name
            song.duration = duration or song.duration
            song.thumbnail_url = thumbnail_url or song.thumbnail_url
            song.audio_url = audio_url or song.audio_url
            song.stream_urls = stream_urls or song.stream_urls
            song.language = raw_song.get("language") or song.language
            song.genre = raw_song.get("genres") or song.genre
            song.is_explicit = raw_song.get("is_explicit", False)
        else:
            song = Song(
                external_id=seokey,
                title=title,
                artist_id=artist.id,
                artist_name=artist_name,
                album_id=album.id,
                album_name=album_name,
                duration=duration,
                thumbnail_url=thumbnail_url,
                audio_url=audio_url,
                stream_urls=stream_urls,
                source="gaana",
                language=raw_song.get("language") or "English",
                genre=raw_song.get("genres") or "Pop",
                mood=raw_song.get("mood") or "Chill",
                is_explicit=raw_song.get("is_explicit", False),
                play_count=0
            )
            db.add(song)

        await db.commit()
        await db.refresh(song)
        return song

    async def search_songs(self, db: AsyncSession, query: str, limit: int = 10) -> List[Song]:
        cache_key = f"search:songs:{query}:{limit}"
        cached = await cache_service.get_json(cache_key)
        if cached:
            # Look up by IDs in database
            stmt = select(Song).where(Song.id.in_(cached))
            res = await db.execute(stmt)
            return list(res.scalars().all())

        results = await self.gaana.search_songs(query, limit)
        if isinstance(results, dict) and "error" in results:
            # Fallback to local DB search
            stmt = select(Song).where(or_(Song.title.ilike(f"%{query}%"), Song.artist_name.ilike(f"%{query}%"))).limit(limit)
            res = await db.execute(stmt)
            return list(res.scalars().all())

        songs: List[Song] = []
        if isinstance(results, list):
            for raw in results:
                if isinstance(raw, dict) and "seokey" in raw:
                    song = await self.upsert_gaana_song(db, raw)
                    songs.append(song)

        if songs:
            await cache_service.set_json(cache_key, [s.id for s in songs], ttl_seconds=1800)
        return songs

    async def search_albums(self, db: AsyncSession, query: str, limit: int = 10) -> List[Album]:
        """
        Albums matching `query`, upserted locally so later requests need no
        upstream call. Falls back to a local title/artist match when Gaana is
        unreachable, which is the same contract as `search_songs`.
        """
        cache_key = f"search:albums:{query}:{limit}"
        cached = await cache_service.get_json(cache_key)
        if cached:
            stmt = select(Album).where(Album.id.in_(cached))
            res = await db.execute(stmt)
            return list(res.scalars().all())

        results = await self.gaana.search_albums(query, limit)
        if not isinstance(results, list):
            return await self._local_albums(db, query, limit)

        albums: List[Album] = []
        for raw in results:
            if isinstance(raw, dict) and raw.get("seokey"):
                albums.append(await self._upsert_gaana_album(db, raw))

        if not albums:
            return await self._local_albums(db, query, limit)

        # get_or_create_* only flush; commit so the rows outlive this request.
        await db.commit()
        await cache_service.set_json(cache_key, [a.id for a in albums], ttl_seconds=1800)
        return albums

    async def _local_albums(self, db: AsyncSession, query: str, limit: int) -> List[Album]:
        stmt = select(Album).where(
            or_(Album.title.ilike(f"%{query}%"), Album.artist_name.ilike(f"%{query}%"))
        ).limit(limit)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def _upsert_gaana_album(self, db: AsyncSession, raw: Dict[str, Any]) -> Album:
        images = (raw.get("images") or {}).get("urls") or {}
        cover_url = images.get("large_artwork") or images.get("medium_artwork") or ""
        artist_name = (raw.get("artists") or "").split(",")[0].strip()

        artist = None
        if artist_name:
            artist = await self.get_or_create_artist(
                db,
                name=artist_name,
                seokey=(raw.get("artist_seokeys") or "").split(",")[0].strip() or None,
                external_id=(raw.get("artist_ids") or "").split(",")[0].strip() or None
            )

        # external_id keys off Gaana's album_id so an album found by search and
        # one created while upserting a song resolve to the same row.
        album = await self.get_or_create_album(
            db,
            title=raw.get("title") or "Unknown Album",
            seokey=raw.get("seokey"),
            external_id=raw.get("album_id"),
            cover_url=cover_url,
            artist_id=artist.id if artist else None,
            artist_name=raw.get("artists") or ""
        )

        # Fields get_or_create_album does not set, and which a song-created row
        # would have left empty.
        album.cover_url = album.cover_url or cover_url
        album.language = raw.get("language") or album.language
        album.release_date = raw.get("release_date") or album.release_date
        try:
            track_count = int(raw.get("track_count") or 0)
        except (ValueError, TypeError):
            track_count = 0
        album.track_count = track_count or album.track_count
        return album

    async def search_artists(self, db: AsyncSession, query: str, limit: int = 10) -> List[Artist]:
        """Artists matching `query`, with the same upsert/fallback contract."""
        cache_key = f"search:artists:{query}:{limit}"
        cached = await cache_service.get_json(cache_key)
        if cached:
            stmt = select(Artist).where(Artist.id.in_(cached))
            res = await db.execute(stmt)
            return list(res.scalars().all())

        results = await self.gaana.search_artists(query, limit)
        if not isinstance(results, list):
            return await self._local_artists(db, query, limit)

        artists: List[Artist] = []
        for raw in results:
            if isinstance(raw, dict) and raw.get("name"):
                images = (raw.get("images") or {}).get("urls") or {}
                artist = await self.get_or_create_artist(
                    db,
                    name=raw["name"],
                    seokey=raw.get("seokey"),
                    external_id=raw.get("artist_id"),
                    image_url=images.get("large_artwork") or images.get("medium_artwork") or ""
                )
                for field in ("song_count", "album_count"):
                    try:
                        value = int(raw.get(field) or 0)
                    except (ValueError, TypeError):
                        value = 0
                    if value:
                        setattr(artist, field, value)
                artists.append(artist)

        if not artists:
            return await self._local_artists(db, query, limit)

        await db.commit()
        await cache_service.set_json(cache_key, [a.id for a in artists], ttl_seconds=1800)
        return artists

    async def _local_artists(self, db: AsyncSession, query: str, limit: int) -> List[Artist]:
        stmt = select(Artist).where(Artist.name.ilike(f"%{query}%")).limit(limit)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def get_song_by_id(self, db: AsyncSession, song_id: str) -> Optional[Song]:
        # song_id is either one of our own UUIDs or a Gaana seokey such as
        # "simtaangaran". Song.id is a native uuid on PostgreSQL, so only
        # compare against it when the value actually parses -- otherwise the
        # bind raises and an unknown song 500s instead of 404ing.
        conditions = [Song.external_id == song_id]
        if is_uuid(song_id):
            conditions.append(Song.id == song_id)
        stmt = select(Song).where(or_(*conditions))
        res = await db.execute(stmt)
        song = res.scalar_one_or_none()
        if song:
            return song

        # Try fetching from Gaana
        raw = await self.gaana.get_track_info([song_id])
        if isinstance(raw, list) and len(raw) > 0 and isinstance(raw[0], dict) and "seokey" in raw[0]:
            return await self.upsert_gaana_song(db, raw[0])
        return None

    async def get_trending(self, db: AsyncSession, language: str = "English", limit: int = 10) -> List[Song]:
        cache_key = f"catalog:trending:{language}:{limit}"
        cached = await cache_service.get_json(cache_key)
        if cached:
            stmt = select(Song).where(Song.id.in_(cached))
            res = await db.execute(stmt)
            return list(res.scalars().all())

        try:
            raw = await asyncio.wait_for(
                self.gaana.get_trending(language, limit), timeout=TRENDING_FETCH_TIMEOUT_SECONDS
            )
        except Exception:
            logger.warning("trending fetch for %s timed out/failed", language, exc_info=True)
            raw = None
        songs: List[Song] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and "seokey" in item:
                    songs.append(await self.upsert_gaana_song(db, item))

        if not songs:
            stmt = select(Song).where(Song.language == language).order_by(Song.play_count.desc()).limit(limit)
            res = await db.execute(stmt)
            songs = list(res.scalars().all())

        if songs:
            await cache_service.set_json(cache_key, [s.id for s in songs], ttl_seconds=1800)
        return songs

    async def get_new_releases(self, db: AsyncSession, language: str = "English", limit: int = 10) -> List[Song]:
        cache_key = f"catalog:newreleases:{language}:{limit}"
        cached = await cache_service.get_json(cache_key)
        if cached:
            stmt = select(Song).where(Song.id.in_(cached))
            res = await db.execute(stmt)
            return list(res.scalars().all())

        try:
            raw = await asyncio.wait_for(
                self.gaana.get_new_releases(language, limit), timeout=TRENDING_FETCH_TIMEOUT_SECONDS
            )
        except Exception:
            logger.warning("new releases fetch for %s timed out/failed", language, exc_info=True)
            raw = None
        songs: List[Song] = []
        if isinstance(raw, dict) and "tracks" in raw and isinstance(raw["tracks"], list):
            for item in raw["tracks"]:
                if isinstance(item, dict) and "seokey" in item:
                    songs.append(await self.upsert_gaana_song(db, item))

        if not songs:
            stmt = select(Song).where(Song.language == language).order_by(Song.created_at.desc()).limit(limit)
            res = await db.execute(stmt)
            songs = list(res.scalars().all())

        if songs:
            await cache_service.set_json(cache_key, [s.id for s in songs], ttl_seconds=1800)
        return songs


catalog_service = CatalogService()
