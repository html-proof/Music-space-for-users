"""Background catalog warm-up: seeds real trending songs across languages.

Gaana exposes no "list languages" endpoint (see
OnboardingService.get_languages), so the onboarding language list is derived
from what has actually been ingested into `songs`. On a fresh or just-cleared
catalog that list is whatever the first few searches happened to touch --
often one or two languages -- which reads as broken rather than merely early.

This fetches real Gaana trending tracks across a broad set of commonly used
languages, through the exact same upsert path search/browsing already uses.
Nothing here is a fabricated or hardcoded "language" record: every row this
writes came from a real Gaana response for that language, and a language
that already has coverage is skipped rather than re-touched.
"""
import asyncio
import logging
from typing import Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import async_session_factory
from app.models.song import Song
from app.services.catalog_service import catalog_service

logger = logging.getLogger("catalog_warmup")

# Broad coverage of languages Gaana serves trending charts for. This is a
# fetch target list for real ingestion, not a UI preset -- nothing here is
# ever shown to a user unless a real song in that language actually lands in
# `songs` below.
WARMUP_LANGUAGES = (
    "Hindi", "English", "Malayalam", "Tamil", "Telugu", "Kannada", "Punjabi",
    "Bengali", "Marathi", "Gujarati", "Bhojpuri", "Odia", "Haryanvi",
    "Rajasthani", "Urdu",
)
SONGS_PER_LANGUAGE = 10
INITIAL_DELAY_SECONDS = 60
RECHECK_INTERVAL_SECONDS = 6 * 3600
# Below this many distinct languages already ingested, a warm-up pass is
# worth running; above it, onboarding has enough real breadth to work with.
MIN_LANGUAGES_TARGET = 10


async def distinct_languages(db: AsyncSession) -> Set[str]:
    rows = (
        await db.execute(
            select(Song.language).where(Song.language.isnot(None), Song.language != "").distinct()
        )
    ).all()
    return {r[0] for r in rows}


async def warm_once(db: AsyncSession) -> int:
    """Fetches trending for every WARMUP_LANGUAGE not yet in the catalog.

    Pure logic over a caller-supplied session, same split as
    release_watch_worker.check_new_releases_once -- directly testable, and
    usable from a request-scoped session if ever needed, not just the loop's
    own. Returns how many languages were attempted.
    """
    existing = await distinct_languages(db)
    missing = [lang for lang in WARMUP_LANGUAGES if lang not in existing]
    for language in missing:
        try:
            # get_trending already bounds its own Gaana call (8s) and upserts
            # through the normal catalog path -- this just drives it across
            # languages nothing has touched yet.
            await catalog_service.get_trending(db, language, limit=SONGS_PER_LANGUAGE)
        except Exception:
            logger.warning("catalog warmup failed for %s", language, exc_info=True)
    logger.info("catalog warmup pass: attempted %d language(s)", len(missing))
    return len(missing)


async def run_once() -> None:
    """Opens its own session -- used by the loop below, mirroring
    release_watch_worker.run_once."""
    async with async_session_factory() as db:
        count = len(await distinct_languages(db))
        if count < MIN_LANGUAGES_TARGET:
            logger.info("catalog warmup: %d language(s) ingested, warming up", count)
            await warm_once(db)
        else:
            logger.info("catalog warmup: %d language(s) already ingested, skipping", count)


async def catalog_warmup_loop() -> None:
    """Runs once shortly after startup, then rechecks periodically.

    Cheap to run repeatedly: `warm_once` only ever fetches a language that is
    still missing, and stops attempting anything once MIN_LANGUAGES_TARGET is
    reached.
    """
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("catalog warmup pass failed; will retry next interval")
        await asyncio.sleep(RECHECK_INTERVAL_SECONDS)
