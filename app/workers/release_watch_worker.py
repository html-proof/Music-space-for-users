"""Discover new songs by followed artists and notify their followers.

There is no "list new releases for artist X" call in the Gaana client
(api/artists/artists.py exposes only search/info/top-tracks/similar), so this
polls each followed artist's current top tracks and treats any track whose
`external_id` (Gaana seokey) is not already in our `songs` table as new. That
existence check must happen *before* the catalog upsert an ad-hoc user search
or play would also trigger -- otherwise every song a user searches for would
look "newly discovered" the first time anyone touches it, which is not the
same thing as "the artist just released it".

This is a best-effort signal, not a guarantee: a song can only be discovered
once it appears in an artist's top tracks on Gaana's side, and a very prolific
artist could have a release fall out of "top tracks" before a slow-interval
poll ever sees it. Good enough for "new release" notifications; not a
substitute for a real ingestion feed.
"""
import asyncio
import logging

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.database import async_session_factory
from app.models.song import Artist, FollowedArtist, Song
from app.services.catalog_queue import catalog_queue
from app.services.catalog_service import catalog_service
from app.services.notification_service import notification_service

logger = logging.getLogger("release_watch_worker")

INITIAL_DELAY_SECONDS = 180
ERROR_BACKOFF_SECONDS = 900
# How many of an artist's current top tracks to inspect per pass.
TRACKS_PER_ARTIST = 10


async def check_new_releases_once(db: AsyncSession) -> dict:
    """One pass over every followed artist, against the given session.

    Takes `db` explicitly (rather than opening its own) so it can run inside
    a caller-managed session -- a test's isolated database via FastAPI's
    `get_db` override, or the standalone script/loop's own session via
    `run_once` below.
    """
    artist_ids = list(
        (await db.execute(select(distinct(FollowedArtist.artist_id)))).scalars().all()
    )

    artists_checked = 0
    new_songs = 0
    notified = 0

    for artist_id in artist_ids:
        artist = (await db.execute(select(Artist).where(Artist.id == artist_id))).scalar_one_or_none()
        if not artist or not artist.external_id:
            continue
        artists_checked += 1

        try:
            raw = await catalog_service.gaana.get_top_tracks(artist.external_id, limit=TRACKS_PER_ARTIST, page=1)
        except Exception:
            logger.exception("failed to fetch top tracks for artist %s (%s)", artist.name, artist.external_id)
            continue

        tracks = raw.get("tracks") if isinstance(raw, dict) else None
        if not isinstance(tracks, list):
            continue

        for raw_track in tracks:
            if not isinstance(raw_track, dict):
                continue
            seokey = raw_track.get("seokey")
            if not seokey:
                continue

            # "Not in the songs table" is the whole discovery test, so a track
            # sitting unwritten in the catalog queue would read as brand new
            # and re-notify every follower on each pass.
            await catalog_queue.ensure_kind_persisted(db, "song")
            existing = (
                await db.execute(select(Song.id).where(Song.external_id == seokey))
            ).scalar_one_or_none()
            if existing:
                continue

            try:
                song = await catalog_service.upsert_gaana_song(db, raw_track)
                # The notification below stores song_id as an FK, and this
                # worker is the one path where the row is wanted immediately
                # rather than at the next flush.
                await catalog_queue.ensure_persisted(db, song.id)
            except Exception:
                logger.exception("failed to upsert newly discovered song %s", seokey)
                continue
            new_songs += 1

            notified += await notification_service.notify_new_song(db, song)

    summary = {
        "artists_followed": len(artist_ids),
        "artists_checked": artists_checked,
        "new_songs": new_songs,
        "notifications_sent": notified,
    }
    logger.info("release watch pass complete: %s", summary)
    return summary


async def run_once() -> dict:
    """Open a fresh session and run one pass -- the entry point for the
    standalone script and the in-process loop, neither of which has a
    request-scoped session to reuse."""
    async with async_session_factory() as db:
        return await check_new_releases_once(db)


async def release_watch_loop() -> None:
    interval = max(int(settings.RELEASE_WATCH_INTERVAL_MINUTES), 15) * 60
    logger.info("Release watch loop started: every %d minutes", interval // 60)
    await asyncio.sleep(INITIAL_DELAY_SECONDS)

    while True:
        try:
            await run_once()
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("Release watch loop cancelled")
            raise
        except Exception:
            logger.exception("release watch pass failed; backing off")
            await asyncio.sleep(ERROR_BACKOFF_SECONDS)
