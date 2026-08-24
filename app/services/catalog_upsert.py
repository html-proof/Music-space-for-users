"""Batched write path for Gaana tracks.

Songs rows exist so a Gaana track has a stable local id for likes, history and
playlists to reference. Writing them is pure overhead on the read path, so it
has to cost as little as possible.

It used to cost a great deal. `upsert_gaana_song` handled exactly one track and
issued, per track, a SELECT for the song, a SELECT for its artist, a SELECT for
its album, a COMMIT and a REFRESH. A cold home feed pulls a couple of hundred
tracks across its shelves, which came to ~494 statements and ~100 commits for a
single request -- every one a round trip to a managed Postgres in another
region. That, not Gaana, is what pushed the home feed past the client's 45s
timeout.

This module does the same work set-at-a-time:

    parse all raws  ->  3 SELECTs (songs, artists, albums by key)
                    ->  build/patch in memory, deduped within the batch
                    ->  1 flush, 1 commit

which is a fixed handful of round trips regardless of batch size.

Two behaviours from the per-song version are preserved deliberately, because
both were bug fixes:

* an album with no Gaana identifier is keyed `single-<song seokey>`, so unrelated
  singles do not all collapse into one album row titled "Single";
* an existing row is patched field by field with `or`, so a sparse payload never
  blanks out data a richer earlier one supplied.
"""
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.song import Album, Artist, Song

logger = logging.getLogger("catalog_upsert")


def _first(value: Optional[str]) -> Optional[str]:
    """First entry of one of Gaana's comma-joined credit strings."""
    if not value:
        return None
    head = str(value).split(",")[0].strip()
    return head or None


def parse_track(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize one raw Gaana track, or None if it carries no usable key."""
    if not isinstance(raw, dict):
        return None
    seokey = raw.get("seokey") or raw.get("track_id")
    if not seokey:
        return None

    try:
        duration = int(raw.get("duration") or 0)
    except (ValueError, TypeError):
        duration = 0

    images = (raw.get("images") or {}).get("urls") or {}
    thumbnail_url = (
        images.get("large_artwork")
        or images.get("medium_artwork")
        or raw.get("artwork_large")
        or ""
    )
    stream_urls = (raw.get("stream_urls") or {}).get("urls") or {}
    audio_url = (
        stream_urls.get("very_high_quality")
        or stream_urls.get("high_quality")
        or stream_urls.get("medium_quality")
        or ""
    )

    artist_credit = raw.get("artists") or raw.get("artist") or "Unknown Artist"
    artist_name = _first(artist_credit) or "Unknown Artist"
    album_title = raw.get("album") or raw.get("album_title") or "Single"
    album_seokey = raw.get("album_seokey")
    # No real album identifier (common for singles). Falling back to matching by
    # title would merge every artist's singles into one row titled "Single", so
    # key off this track instead.
    album_external_id = raw.get("album_id") or album_seokey or f"single-{seokey}"

    return {
        "seokey": str(seokey),
        "title": raw.get("title") or raw.get("track_title") or "Unknown Title",
        "artist_credit": artist_credit,
        "artist_name": artist_name,
        "artist_seokey": _first(raw.get("artist_seokeys")),
        "artist_external_id": (
            _first(raw.get("artist_ids"))
            or _first(raw.get("artist_seokeys"))
            or artist_name.lower().replace(" ", "-")
        ),
        "artist_image": raw.get("artist_image"),
        "album_title": album_title,
        "album_seokey": album_seokey,
        "album_external_id": str(album_external_id),
        "duration": duration,
        "thumbnail_url": thumbnail_url,
        "audio_url": audio_url,
        "stream_urls": stream_urls,
        "language": raw.get("language"),
        "genre": raw.get("genres"),
        "mood": raw.get("mood"),
        "is_explicit": bool(raw.get("is_explicit", False)),
    }


async def _load_artists(
    db: AsyncSession, parsed: List[Dict[str, Any]]
) -> Tuple[Dict[str, Artist], Dict[str, Artist]]:
    """Existing artists for this batch, indexed by external_id and by name.

    Both indexes are needed because `get_or_create_artist` matched on either,
    and rows created by different code paths key on different things.
    """
    ext_ids = {p["artist_external_id"] for p in parsed if p["artist_external_id"]}
    names = {p["artist_name"] for p in parsed if p["artist_name"]}
    if not ext_ids and not names:
        return {}, {}

    rows = (
        await db.execute(
            select(Artist).where(Artist.external_id.in_(ext_ids) | Artist.name.in_(names))
        )
    ).scalars().all()
    return (
        {a.external_id: a for a in rows if a.external_id},
        {a.name: a for a in rows if a.name},
    )


async def _load_albums(db: AsyncSession, parsed: List[Dict[str, Any]]) -> Dict[str, Album]:
    ext_ids = {p["album_external_id"] for p in parsed if p["album_external_id"]}
    if not ext_ids:
        return {}
    rows = (
        await db.execute(select(Album).where(Album.external_id.in_(ext_ids)))
    ).scalars().all()
    return {a.external_id: a for a in rows if a.external_id}


async def upsert_tracks(db: AsyncSession, raws: Iterable[Dict[str, Any]]) -> List[Song]:
    """Upsert every track in `raws`, returning them in the order given.

    Duplicates within the batch resolve to one row, and a track already in the
    database is patched rather than duplicated. Commits once.
    """
    parsed: List[Dict[str, Any]] = []
    seen_seokeys = set()
    for raw in raws or ():
        item = parse_track(raw)
        if item is None or item["seokey"] in seen_seokeys:
            continue
        seen_seokeys.add(item["seokey"])
        parsed.append(item)

    if not parsed:
        return []

    existing_songs = {
        s.external_id: s
        for s in (
            await db.execute(select(Song).where(Song.external_id.in_(seen_seokeys)))
        ).scalars().all()
    }
    artists_by_ext, artists_by_name = await _load_artists(db, parsed)
    albums_by_ext = await _load_albums(db, parsed)

    # --- artists and albums first: songs carry FKs to both.
    for item in parsed:
        ext_id, name = item["artist_external_id"], item["artist_name"]
        artist = artists_by_ext.get(ext_id) or artists_by_name.get(name)
        if artist is None:
            artist = Artist(
                external_id=ext_id,
                name=name,
                seokey=item["artist_seokey"],
                image_url=item["artist_image"],
            )
            db.add(artist)
            artists_by_ext[ext_id] = artist
            artists_by_name[name] = artist
        item["_artist"] = artist

    for item in parsed:
        ext_id = item["album_external_id"]
        album = albums_by_ext.get(ext_id)
        if album is None:
            album = Album(
                external_id=ext_id,
                title=item["album_title"],
                seokey=item["album_seokey"],
                cover_url=item["thumbnail_url"],
                artist_name=item["_artist"].name,
            )
            db.add(album)
            albums_by_ext[ext_id] = album
        item["_album"] = album

    # One flush so artists and albums get their ids before songs reference them.
    await db.flush()
    for item in parsed:
        item["_album"].artist_id = item["_album"].artist_id or item["_artist"].id

    songs: List[Song] = []
    for item in parsed:
        song = existing_songs.get(item["seokey"])
        artist, album = item["_artist"], item["_album"]
        if song is None:
            song = Song(
                external_id=item["seokey"],
                title=item["title"],
                artist_id=artist.id,
                artist_name=item["artist_credit"],
                album_id=album.id,
                album_name=item["album_title"],
                duration=item["duration"],
                thumbnail_url=item["thumbnail_url"],
                audio_url=item["audio_url"],
                stream_urls=item["stream_urls"],
                source="gaana",
                language=item["language"] or "",
                genre=item["genre"] or "",
                mood=item["mood"] or "",
                is_explicit=item["is_explicit"],
                play_count=0,
            )
            db.add(song)
            existing_songs[item["seokey"]] = song
        else:
            # `or` throughout: a sparse payload must never blank a field a
            # richer earlier response filled in.
            song.title = item["title"] or song.title
            song.artist_id = artist.id
            song.artist_name = item["artist_credit"] or song.artist_name
            song.album_id = album.id
            song.album_name = item["album_title"] or song.album_name
            song.duration = item["duration"] or song.duration
            song.thumbnail_url = item["thumbnail_url"] or song.thumbnail_url
            song.audio_url = item["audio_url"] or song.audio_url
            song.stream_urls = item["stream_urls"] or song.stream_urls
            song.language = item["language"] or song.language
            song.genre = item["genre"] or song.genre
            song.mood = item["mood"] or song.mood
            song.is_explicit = item["is_explicit"]
        songs.append(song)

    await db.commit()
    return songs
