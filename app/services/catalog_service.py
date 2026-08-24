import asyncio
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from api.gaanapy import GaanaPy
from app.db.base import is_uuid
from app.models.song import Song, Artist, Album
from app.services.cache_service import cache_service
from app.services.catalog_upsert import upsert_tracks
from app.utils.cache_keys import catalog_languages_key

logger = logging.getLogger("catalog_service")

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

    async def upsert_gaana_songs(
        self, db: AsyncSession, raws: List[Dict[str, Any]]
    ) -> List[Song]:
        """Upsert a whole Gaana response in a fixed handful of round trips.

        See `app.services.catalog_upsert` for why this is set-at-a-time. The
        per-song path it replaces issued three SELECTs, a COMMIT and a REFRESH
        for every single track, which came to ~494 statements and ~100 commits
        for one cold home feed -- each a round trip to a managed Postgres, and
        the real reason the feed exceeded the client timeout.
        """
        return await upsert_tracks(db, raws)

    async def upsert_gaana_song(
        self, db: AsyncSession, raw_song: Dict[str, Any]
    ) -> Optional[Song]:
        """One track, for the callers that genuinely have one in hand (a
        resolved radio seed, a song looked up by id)."""
        songs = await upsert_tracks(db, [raw_song])
        return songs[0] if songs else None

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

        songs = await self.upsert_gaana_songs(db, results if isinstance(results, list) else [])

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
            return await self.cached_songs(db, cached)

        try:
            raw = await asyncio.wait_for(
                self.gaana.get_trending(language, limit), timeout=TRENDING_FETCH_TIMEOUT_SECONDS
            )
        except Exception:
            logger.warning("trending fetch for %s timed out/failed", language, exc_info=True)
            raw = None
        songs = await self.upsert_gaana_songs(db, raw if isinstance(raw, list) else [])

        if songs:
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
        tracks = raw.get("tracks") if isinstance(raw, dict) else None
        songs = await self.upsert_gaana_songs(db, tracks if isinstance(tracks, list) else [])

        if songs:
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

        songs = await self.upsert_gaana_songs(db, results if isinstance(results, list) else [])

        if songs:
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

        songs = await self.upsert_gaana_songs(db, results if isinstance(results, list) else [])

        if songs:
            await cache_service.set_json(cache_key, [s.id for s in songs], ttl_seconds=1800)
        return songs

    # ------------------------------------------------------------ fan-out

    async def fetch_many(
        self, db: AsyncSession, requests: Sequence[Tuple[str, tuple]]
    ) -> List[List[Song]]:
        """Run several catalog fetches, doing the network part concurrently.

        Retrieval needs a handful of these per request -- top tracks for three
        artists, two genres, trending in two languages -- and running them one
        after another meant the home feed waited for the sum of every upstream
        round trip.

        They cannot simply be `gather`ed, because each one writes through the
        same `AsyncSession` and a session is not safe for concurrent use. So the
        two halves are split: every *network* call is issued at once, and the
        rows are then written from the results sequentially, one batch each.
        Cache hits skip the network entirely and are resolved in the same pass.

        `requests` is a sequence of (kind, args); results come back positionally,
        with an empty list wherever a fetch failed.
        """
        planners = {
            "trending": self._plan_trending,
            "new_releases": self._plan_new_releases,
            "artist": self._plan_artist_top_songs,
            "genre": self._plan_genre_or_mood,
            "search": self._plan_search,
        }

        plans = []
        for kind, args in requests:
            planner = planners.get(kind)
            plans.append(None if planner is None else planner(*args))

        # Phase 1: cache lookups and network calls, all at once.
        fetched = await asyncio.gather(
            *[
                plan["fetch"]() if plan else _none()
                for plan in plans
            ],
            return_exceptions=True,
        )

        # Phase 2: writes, sequentially, through the one session.
        out: List[List[Song]] = []
        for plan, result in zip(plans, fetched):
            if plan is None or isinstance(result, BaseException):
                if isinstance(result, BaseException):
                    logger.warning("catalog fetch failed: %s", result)
                out.append([])
                continue
            try:
                out.append(await plan["resolve"](db, result))
            except Exception:
                logger.warning("catalog upsert failed", exc_info=True)
                out.append([])
        return out

    def _plan(self, cache_key: str, fetch_raw, extract):
        """One fetch, split into its network half and its database half."""

        async def fetch():
            cached = await cache_service.get_json(cache_key)
            if cached:
                return {"cached": cached}
            try:
                return {"raw": await asyncio.wait_for(
                    fetch_raw(), timeout=TRENDING_FETCH_TIMEOUT_SECONDS
                )}
            except Exception:
                logger.warning("upstream fetch for %s failed", cache_key, exc_info=True)
                return {"raw": None}

        async def resolve(db: AsyncSession, result: dict) -> List[Song]:
            if "cached" in result:
                return await self.cached_songs(db, result["cached"])
            songs = await self.upsert_gaana_songs(db, extract(result.get("raw")))
            if songs:
                await cache_service.set_json(
                    cache_key, [s.id for s in songs], ttl_seconds=1800
                )
            return songs

        return {"fetch": fetch, "resolve": resolve}

    @staticmethod
    def _as_list(raw) -> List[Dict[str, Any]]:
        return raw if isinstance(raw, list) else []

    @staticmethod
    def _as_tracks(raw) -> List[Dict[str, Any]]:
        tracks = raw.get("tracks") if isinstance(raw, dict) else None
        return tracks if isinstance(tracks, list) else []

    def _plan_trending(self, language: str, limit: int):
        return self._plan(
            f"catalog:trending:{language}:{limit}",
            lambda: self.gaana.get_trending(language, limit),
            self._as_list,
        )

    def _plan_new_releases(self, language: str, limit: int):
        return self._plan(
            f"catalog:newreleases:{language}:{limit}",
            lambda: self.gaana.get_new_releases(language, limit),
            self._as_tracks,
        )

    def _plan_artist_top_songs(self, artist: str, limit: int):
        return self._plan(
            f"catalog:artist_songs:{artist}:{limit}",
            lambda: self.gaana.search_songs(artist, limit),
            self._as_list,
        )

    def _plan_genre_or_mood(self, query: str, language: Optional[str], limit: int):
        term = f"{query} {language}".strip() if language else query
        return self._plan(
            f"catalog:genre_mood:{term}:{limit}",
            lambda: self.gaana.search_songs(term, limit),
            self._as_list,
        )

    def _plan_search(self, query: str, limit: int):
        return self._plan(
            f"search:songs:{query}:{limit}",
            lambda: self.gaana.search_songs(query, limit),
            self._as_list,
        )


async def _none():
    return None


catalog_service = CatalogService()
