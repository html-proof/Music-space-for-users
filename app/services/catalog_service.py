import asyncio
import logging
from typing import List, Optional, Dict, Any
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from api.gaanapy import GaanaPy
from app.config.settings import settings
from app.db.base import is_uuid
from app.models.song import Song, Artist, Album
from app.services.cache_service import cache_service
from app.models.catalog_sync import ALBUM, PRIORITY_BACKGROUND, SONG
from app.services.catalog_queue import catalog_queue, utcnow
from app.services.catalog_sync_service import catalog_sync_service
from app.utils.cache_keys import catalog_languages_key

logger = logging.getLogger("catalog_service")

# Session-scoped buffer of sync jobs to write once the read finishes.
_SYNC_PENDING_KEY = "catalog_sync_pending"

# GaanaPy's own aiohttp session has a 30s timeout, which is fine for a
# foreground search but too generous for a home-feed shelf that must still
# leave room for everything else in the same request (upserts, other
# shelves) inside the client's 45s budget. Bounding just the raw network
# call -- never the DB upserts that follow it -- means a slow/unresponsive
# Gaana falls back to whatever is already cached in Postgres quickly instead
# of stalling the whole response.
TRENDING_FETCH_TIMEOUT_SECONDS = 8.0

# How much of Gaana chart listing to read when discovering languages. The
# endpoint tops out around 130 charts however much is asked for; this is
# comfortably above that so the discovered set is the whole listing rather than
# a popularity-truncated slice of it (which would silently drop the smaller
# languages -- exactly the ones a hardcoded list also gets wrong).
LANGUAGE_DISCOVERY_CHART_LIMIT = 500
# Which languages Gaana curates charts for changes on the order of months.
LANGUAGE_CACHE_TTL_SECONDS = 6 * 60 * 60

# Every read method below returns *only* what Gaana served for this request (or
# a still-valid cached copy of a recent Gaana response). None of them falls back
# to a `select(Song)` scan any more.
#
# The old fallbacks made Postgres a second, silently-authoritative music
# catalog: when Gaana was slow or a language had no coverage, the shelf quietly
# filled up with whatever rows happened to have been ingested earlier, so the
# feed was really being served from the database. Songs rows are still written
# here, but strictly as a by-product -- they exist to give a Gaana track a
# stable local id that likes/history/playlists can reference, and are never a
# retrieval source. An empty list is the honest answer when Gaana has nothing;
# the caller turns that into an empty/retry state.


class CatalogService:
    def __init__(self):
        self.gaana = GaanaPy()

    async def close(self):
        if hasattr(self.gaana, '_aiohttp') and self.gaana._aiohttp and not self.gaana._aiohttp.closed:
            await self.gaana._aiohttp.close()

    async def _insert_or_get(self, db: AsyncSession, stmt, build):
        """Insert a row, tolerating a concurrent request inserting it first.

        Two searches in flight at once both miss on the same Gaana artist or
        album and both try to INSERT it, so the loser hits the unique index on
        external_id and its IntegrityError kills the whole request. Doing the
        INSERT inside a SAVEPOINT means the violation rolls back only that
        statement -- the surrounding transaction stays usable -- and the row
        the winner committed is then found by re-running the same lookup.
        """
        res = await db.execute(stmt)
        row = res.scalars().first()
        if row is not None:
            return row
        obj = build()
        try:
            async with db.begin_nested():
                db.add(obj)
                await db.flush()
            return obj
        except IntegrityError:
            res = await db.execute(stmt)
            row = res.scalars().first()
            if row is None:
                raise
            return row

    async def get_or_create_artist(self, db: AsyncSession, name: str, seokey: Optional[str] = None, external_id: Optional[str] = None, image_url: Optional[str] = None) -> Artist:
        ext_id = external_id or seokey or name.lower().replace(" ", "-")
        stmt = select(Artist).where(or_(Artist.external_id == ext_id, Artist.name == name))
        return await self._insert_or_get(
            db,
            stmt,
            lambda: Artist(
                external_id=ext_id,
                name=name,
                seokey=seokey,
                image_url=image_url
            ),
        )

    async def get_or_create_album(self, db: AsyncSession, title: str, seokey: Optional[str] = None, external_id: Optional[str] = None, cover_url: Optional[str] = None, artist_id: Optional[str] = None, artist_name: str = "") -> Album:
        ext_id = external_id or seokey or title.lower().replace(" ", "-")
        stmt = select(Album).where(or_(Album.external_id == ext_id, Album.title == title))
        return await self._insert_or_get(
            db,
            stmt,
            lambda: Album(
                external_id=ext_id,
                title=title,
                seokey=seokey,
                cover_url=cover_url,
                artist_id=artist_id,
                artist_name=artist_name
            ),
        )

    @staticmethod
    def _parse_gaana_song(raw_song: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten one raw Gaana track into the fields our tables store.

        Split out from the write so both the queued and the synchronous path
        parse identically -- the queued path needs the parsed values without a
        Song instance to hang them on.
        """
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

        primary_artist = artist_name.split(",")[0].strip() if artist_name else "Unknown Artist"
        artist_seokey = raw_song.get("artist_seokeys", "").split(",")[0].strip() or None
        artist_ext_id = raw_song.get("artist_ids", "").split(",")[0].strip() or None

        album_seokey = raw_song.get("album_seokey")
        album_ext_id = raw_song.get("album_id") or album_seokey
        if not album_ext_id:
            # No real album identifier from Gaana (common for singles), and the
            # album lookup falls back to matching by title when there is none --
            # every such track shares the literal title "Single", which would
            # otherwise merge unrelated singles from every artist into one
            # shared "Single" album. Keying off this song own seokey instead
            # keeps each single its own album, one row per song rather than one
            # row for the whole catalog.
            album_ext_id = f"single-{seokey}"

        return {
            "seokey": seokey,
            "title": title,
            "artist_name": artist_name,
            "album_name": album_name,
            "duration": duration,
            "thumbnail_url": thumbnail_url,
            "stream_urls": stream_urls,
            "audio_url": audio_url,
            "primary_artist": primary_artist,
            "artist_seokey": artist_seokey,
            "artist_ext_id": artist_ext_id or artist_seokey or primary_artist.lower().replace(" ", "-"),
            "artist_image": raw_song.get("artist_image"),
            "album_seokey": album_seokey,
            "album_ext_id": album_ext_id,
            "language": raw_song.get("language"),
            "genre": raw_song.get("genres"),
            "mood": raw_song.get("mood"),
            "is_explicit": raw_song.get("is_explicit", False),
        }

    async def upsert_gaana_song(self, db: AsyncSession, raw_song: Dict[str, Any]) -> Song:
        """Return the local Song row for a raw Gaana track, creating it if new.

        By default the write itself is queued (see app/services/catalog_queue)
        and the returned Song is a transient object carrying the id the row
        will have; the caller needs the id and the parsed fields to build its
        response, not the durability of the row. Set
        CATALOG_WRITE_QUEUE_ENABLED=false to write through synchronously.
        """
        p = self._parse_gaana_song(raw_song)
        if settings.CATALOG_WRITE_QUEUE_ENABLED:
            return await self._queue_gaana_song(db, p)

        artist = await self.get_or_create_artist(
            db,
            name=p["primary_artist"],
            seokey=p["artist_seokey"],
            external_id=p["artist_ext_id"],
            image_url=p["artist_image"],
        )
        album = await self.get_or_create_album(
            db,
            title=p["album_name"],
            seokey=p["album_seokey"],
            external_id=p["album_ext_id"],
            cover_url=p["thumbnail_url"],
            artist_id=artist.id,
            artist_name=artist.name,
        )

        stmt = select(Song).where(Song.external_id == p["seokey"])
        song = (await db.execute(stmt)).scalar_one_or_none()

        if song:
            song.title = p["title"]
            song.artist_id = artist.id
            song.artist_name = p["artist_name"]
            song.album_id = album.id
            song.album_name = p["album_name"]
            song.duration = p["duration"] or song.duration
            song.thumbnail_url = p["thumbnail_url"] or song.thumbnail_url
            song.audio_url = p["audio_url"] or song.audio_url
            song.stream_urls = p["stream_urls"] or song.stream_urls
            song.language = p["language"] or song.language
            song.genre = p["genre"] or song.genre
            song.is_explicit = p["is_explicit"]
        else:
            song = Song(
                external_id=p["seokey"],
                title=p["title"],
                artist_id=artist.id,
                artist_name=p["artist_name"],
                album_id=album.id,
                album_name=p["album_name"],
                duration=p["duration"],
                thumbnail_url=p["thumbnail_url"],
                audio_url=p["audio_url"],
                stream_urls=p["stream_urls"],
                source="gaana",
                language=p["language"] or "English",
                genre=p["genre"] or "Pop",
                mood=p["mood"] or "Chill",
                is_explicit=p["is_explicit"],
                play_count=0
            )
            try:
                async with db.begin_nested():
                    db.add(song)
                    await db.flush()
            except IntegrityError:
                # Another in-flight request inserted this same Gaana track
                # first; the savepoint rollback left the transaction usable,
                # so take its row rather than failing the request.
                res = await db.execute(select(Song).where(Song.external_id == p["seokey"]))
                existing = res.scalar_one_or_none()
                if existing is None:
                    raise
                return existing

        await db.commit()
        await db.refresh(song)
        return song

    async def _queue_gaana_song(self, db: AsyncSession, p: Dict[str, Any]) -> Song:
        artist_id, artist_ext, _ = await catalog_queue.resolve_id(
            db, "artist", p["artist_ext_id"], match_name=p["primary_artist"]
        )
        await catalog_queue.enqueue("artist", {
            "id": artist_id,
            "external_id": artist_ext,
            "name": p["primary_artist"],
            "seokey": p["artist_seokey"],
            "image_url": p["artist_image"],
        })

        album_id, album_ext, album_is_new = await catalog_queue.resolve_id(
            db, "album", p["album_ext_id"], match_name=p["album_name"]
        )
        await catalog_queue.enqueue("album", {
            "id": album_id,
            "external_id": album_ext,
            "title": p["album_name"],
            "seokey": p["album_seokey"],
            "cover_url": p["thumbnail_url"],
            "artist_id": artist_id,
            "artist_name": p["primary_artist"],
        })

        song_id, song_ext, song_is_new = await catalog_queue.resolve_id(db, "song", p["seokey"])
        values = {
            "id": song_id,
            "external_id": song_ext,
            "title": p["title"],
            "artist_id": artist_id,
            "artist_name": p["artist_name"],
            "album_id": album_id,
            "album_name": p["album_name"],
            "duration": p["duration"],
            "thumbnail_url": p["thumbnail_url"],
            "audio_url": p["audio_url"],
            "stream_urls": p["stream_urls"],
            "source": "gaana",
            "language": p["language"] or "English",
            "genre": p["genre"] or "Pop",
            "mood": p["mood"] or "Chill",
            "is_explicit": p["is_explicit"],
        }
        await catalog_queue.enqueue("song", values)

        # Transient: deliberately not added to `db`, so the session never tries
        # to write it a second time. It carries every column the response
        # schemas read, and created_at -- which the row will get from the
        # server default -- is filled in so a freshly queued song does not
        # serialize with a null timestamp.
        song = Song(**values, play_count=0)
        song.created_at = song.updated_at = utcnow()

        # An id that has never existed as a row is the one thing a process
        # restart could strand: the client holds it, and only this process
        # knows what it stands for. Record a durable sync job for it so the
        # worker can fetch and store the entity under that same id even if the
        # in-memory batch never gets flushed. Buffered, not written here, so a
        # page of results costs one INSERT rather than twenty.
        if song_is_new or album_is_new:
            pending = db.info.setdefault(_SYNC_PENDING_KEY, [])
            if song_is_new:
                pending.append({
                    "entity_type": SONG,
                    "external_id": song_ext,
                    "entity_id": song_id,
                    "priority": PRIORITY_BACKGROUND,
                })
            if album_is_new and not p["album_ext_id"].startswith("single-"):
                # A synthetic "single-<track>" key is not an album on Gaana and
                # would fail every fetch; real album ids only.
                pending.append({
                    "entity_type": ALBUM,
                    "external_id": album_ext,
                    "entity_id": album_id,
                    "priority": PRIORITY_BACKGROUND,
                })
        return song

    @staticmethod
    async def register_sync_jobs(db: AsyncSession) -> int:
        """Persist the sync jobs buffered by `_queue_gaana_song` in this session.

        Called once per catalog read that upserts songs, so the durable record
        of "these ids exist and still need their rows" is committed before the
        response goes out.
        """
        pending = db.info.pop(_SYNC_PENDING_KEY, None)
        if not pending or not settings.CATALOG_SYNC_ENABLED:
            return 0
        try:
            return await catalog_sync_service.enqueue_many(db, pending)
        except Exception:
            # The catalog read itself must not fail because bookkeeping did.
            logger.warning("could not register catalog sync jobs", exc_info=True)
            await db.rollback()
            return 0

    async def get_languages(self) -> List[str]:
        """The languages Gaana actually serves, discovered from Gaana.

        There is no "list languages" endpoint, but the top-charts listing is
        one: Gaana curates charts per language and stamps each with the
        language it belongs to, so the distinct set across the whole listing is
        Gaana own answer to the question. Ordered by how many charts each has,
        which is Gaana own signal for how much catalog sits behind it.

        This replaced two earlier answers, both wrong in the same way. First
        `SELECT DISTINCT language FROM songs`, which made the onboarding screen
        a function of what had been ingested -- empty on a fresh deployment.
        Then a hardcoded list in config, which was a preset: the names were
        ours, not Gaana, and nothing kept them true.

        Returns `[]` when Gaana is unreachable. The caller surfaces that as an
        empty/retry state rather than substituting a default list.
        """
        cached = await cache_service.get_json(catalog_languages_key())
        if isinstance(cached, list) and cached:
            return [str(name) for name in cached]

        try:
            charts = await asyncio.wait_for(
                self.gaana.get_charts(LANGUAGE_DISCOVERY_CHART_LIMIT),
                timeout=TRENDING_FETCH_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning("language discovery via charts failed", exc_info=True)
            return []

        if not isinstance(charts, list):
            logger.warning("language discovery: charts unavailable upstream")
            return []

        counts: Dict[str, int] = {}
        for chart in charts:
            if not isinstance(chart, dict):
                continue
            # A chart may be stamped with several languages ("Tamil,Kannada,
            # Telugu,Malayalam" is a real one), which is a listing of languages,
            # not a language.
            for part in str(chart.get("language") or "").split(","):
                name = part.strip()
                if name:
                    counts[name] = counts.get(name, 0) + 1

        if not counts:
            return []

        languages = [
            name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        await cache_service.set_json(
            catalog_languages_key(), languages, ttl_seconds=LANGUAGE_CACHE_TTL_SECONDS
        )
        return languages

    async def default_languages(self, limit: int = 2) -> List[str]:
        """Which languages to query for a user who has stated no preference.

        Gaana own ordering, not ours: `get_languages` ranks by how many charts
        Gaana curates per language, so this is "whatever Gaana has most of"
        rather than a house default. The call sites here used to hardcode
        "English" (or "Hindi, English"), which quietly made the cold-start feed
        a product decision baked into the source.

        Empty when Gaana is unreachable, which leaves the feed empty rather than
        populated with something nobody asked for.
        """
        return (await self.get_languages())[:limit]

    async def resolve_language(self, name: Optional[str]) -> Optional[str]:
        """Gaana own spelling of `name`, or None if Gaana does not serve it.

        Case- and whitespace-insensitive, so a client sending "malayalam" still
        resolves. Returning None is what stops arbitrary client input from being
        stored as a preference and later sent upstream as a language.
        """
        if not name:
            return None
        wanted = name.strip().lower()
        for known in await self.get_languages():
            if known.lower() == wanted:
                return known
        return None

    async def cached_songs(self, db: AsyncSession, cached_ids: list) -> List[Song]:
        """Rehydrate a cached Gaana result, preserving Gaana's ordering.

        The cache stores ids, not rows, so the songs have to be read back by
        primary key -- `IN (...)` returns them in whatever order Postgres
        likes, which silently discarded the ranking Gaana sent (the whole
        point of a "trending" shelf). Reordering here restores it.
        """
        if not cached_ids:
            return []
        # These ids were handed out before their rows were written, so a cache
        # entry made in the last flush interval can point at rows that are
        # still queued. Flushing first is what keeps a cache hit from silently
        # returning fewer songs than it cached.
        await catalog_queue.ensure_persisted(db, *[str(sid) for sid in cached_ids])
        rows = (await db.execute(select(Song).where(Song.id.in_(cached_ids)))).scalars().all()
        by_id = {str(row.id): row for row in rows}
        return [by_id[str(sid)] for sid in cached_ids if str(sid) in by_id]

    async def search_songs(self, db: AsyncSession, query: str, limit: int = 10) -> List[Song]:
        cache_key = f"search:songs:{query}:{limit}"
        cached = await cache_service.get_json(cache_key)
        if cached:
            return await self.cached_songs(db, cached)

        results = await self.gaana.search_songs(query, limit)
        if isinstance(results, dict) and "error" in results:
            logger.warning("song search for %r failed upstream: %s", query, results.get("error"))
            return []

        songs: List[Song] = []
        if isinstance(results, list):
            for raw in results:
                if isinstance(raw, dict) and "seokey" in raw:
                    song = await self.upsert_gaana_song(db, raw)
                    songs.append(song)

        if songs:
            # Durable record of the ids just handed out, before the response
            # carrying them leaves.
            await self.register_sync_jobs(db)
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
            logger.warning("album search for %r unavailable upstream", query)
            return []

        albums: List[Album] = []
        for raw in results:
            if isinstance(raw, dict) and raw.get("seokey"):
                albums.append(await self._upsert_gaana_album(db, raw))

        if not albums:
            return []

        # get_or_create_* only flush; commit so the rows outlive this request.
        await db.commit()
        await cache_service.set_json(cache_key, [a.id for a in albums], ttl_seconds=1800)
        return albums

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
            logger.warning("artist search for %r unavailable upstream", query)
            return []

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
            return []

        await db.commit()
        await cache_service.set_json(cache_key, [a.id for a in artists], ttl_seconds=1800)
        return artists

    async def get_song_by_id(self, db: AsyncSession, song_id: str) -> Optional[Song]:
        # song_id is either one of our own UUIDs or a Gaana seokey such as
        # "simtaangaran". Song.id is a native uuid on PostgreSQL, so only
        # compare against it when the value actually parses -- otherwise the
        # bind raises and an unknown song 500s instead of 404ing.
        # A song the queue has not written yet has no row to find, so flush
        # first if this id is one of those. No-op otherwise.
        await catalog_queue.ensure_persisted(db, song_id)

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
            song = await self.upsert_gaana_song(db, raw[0])
            await self.register_sync_jobs(db)
            return song
        return None

    async def get_trending(self, db: AsyncSession, language: str = "English", limit: int = 10) -> List[Song]:
        cache_key = f"catalog:trending:{language}:{limit}"
        cached = await cache_service.get_json(cache_key)
        if cached:
            return await self.cached_songs(db, cached)

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

        if songs:
            # Durable record of the ids just handed out, before the response
            # carrying them leaves.
            await self.register_sync_jobs(db)
            await cache_service.set_json(cache_key, [s.id for s in songs], ttl_seconds=1800)
        return songs

    async def get_new_releases(self, db: AsyncSession, language: str = "English", limit: int = 10) -> List[Song]:
        cache_key = f"catalog:newreleases:{language}:{limit}"
        cached = await cache_service.get_json(cache_key)
        if cached:
            return await self.cached_songs(db, cached)

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

        if songs:
            # Durable record of the ids just handed out, before the response
            # carrying them leaves.
            await self.register_sync_jobs(db)
            await cache_service.set_json(cache_key, [s.id for s in songs], ttl_seconds=1800)
        return songs

    async def get_artist_top_songs(self, db: AsyncSession, artist_name_or_id: str, limit: int = 10) -> List[Song]:
        cache_key = f"catalog:artist_songs:{artist_name_or_id}:{limit}"
        cached = await cache_service.get_json(cache_key)
        if cached:
            return await self.cached_songs(db, cached)

        results = None
        try:
            results = await asyncio.wait_for(
                self.gaana.search_songs(artist_name_or_id, limit),
                timeout=TRENDING_FETCH_TIMEOUT_SECONDS
            )
        except Exception:
            logger.warning("artist songs fetch for %s timed out/failed", artist_name_or_id, exc_info=True)

        songs: List[Song] = []
        if isinstance(results, list):
            for raw in results:
                if isinstance(raw, dict) and "seokey" in raw:
                    song = await self.upsert_gaana_song(db, raw)
                    songs.append(song)

        if songs:
            # Durable record of the ids just handed out, before the response
            # carrying them leaves.
            await self.register_sync_jobs(db)
            await cache_service.set_json(cache_key, [s.id for s in songs], ttl_seconds=1800)
        return songs

    async def get_genre_or_mood_songs(self, db: AsyncSession, query: str, language: Optional[str] = None, limit: int = 10) -> List[Song]:
        search_term = f"{query} {language}".strip() if language else query
        cache_key = f"catalog:genre_mood:{search_term}:{limit}"
        cached = await cache_service.get_json(cache_key)
        if cached:
            return await self.cached_songs(db, cached)

        results = None
        try:
            results = await asyncio.wait_for(
                self.gaana.search_songs(search_term, limit),
                timeout=TRENDING_FETCH_TIMEOUT_SECONDS
            )
        except Exception:
            logger.warning("genre/mood fetch for %s timed out/failed", search_term, exc_info=True)

        songs: List[Song] = []
        if isinstance(results, list):
            for raw in results:
                if isinstance(raw, dict) and "seokey" in raw:
                    song = await self.upsert_gaana_song(db, raw)
                    songs.append(song)

        if songs:
            # Durable record of the ids just handed out, before the response
            # carrying them leaves.
            await self.register_sync_jobs(db)
            await cache_service.set_json(cache_key, [s.id for s in songs], ttl_seconds=1800)
        return songs


catalog_service = CatalogService()
