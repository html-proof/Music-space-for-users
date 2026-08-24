import logging

from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.config.settings import settings
from app.db.database import get_db
from app.models.catalog_sync import ALBUM, PRIORITY_REQUESTED
from app.services.catalog_service import catalog_service
from app.services.catalog_sync_service import catalog_sync_service
from app.utils.response import api_response, api_error

logger = logging.getLogger("catalog_api")

router = APIRouter(prefix="/api/catalog", tags=["Catalog & Stream Extraction"])


@router.get("/songs/search", summary="Search raw songs from Gaana catalog")
async def search_gaana_songs(
    query: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=100)
):
    result = await catalog_service.gaana.search_songs(query, limit)
    if isinstance(result, dict) and "error" in result:
        return api_error("NOT_FOUND", result["error"], status_code=404)
    return api_response(result)


@router.get("/songs/info", summary="Retrieve decrypted stream URLs and song info")
async def get_gaana_song_info(
    seokey: str = Query(..., min_length=1, max_length=200)
):
    result = await catalog_service.gaana.get_track_info([seokey])
    if isinstance(result, dict) and "error" in result:
        return api_error("NOT_FOUND", result["error"], status_code=404)
    return api_response(result)


@router.get("/albums/info", summary="Retrieve album info with decrypted tracks")
async def get_gaana_album_info(
    seokey: str = Query(..., min_length=1, max_length=200),
    db: AsyncSession = Depends(get_db)
):
    result = await catalog_service.gaana.get_album_info([seokey], True)
    if isinstance(result, dict) and "error" in result:
        return api_error("NOT_FOUND", result["error"], status_code=404)

    # Opening an album is the request that puts it in the sync queue: the
    # worker stores the album and queues one job per track, so the album is
    # mirrored locally whether or not the user plays anything from it. Queued,
    # not awaited -- the response is already complete without it.
    if settings.CATALOG_SYNC_ENABLED:
        try:
            await catalog_sync_service.enqueue(
                db, ALBUM, seokey, priority=PRIORITY_REQUESTED
            )
        except Exception:
            logger.warning("could not queue album %s for sync", seokey, exc_info=True)
            await db.rollback()
    return api_response(result)


@router.get("/sync/status", summary="Catalog synchronization queue depth by status")
async def get_sync_status(db: AsyncSession = Depends(get_db)):
    """How much catalog work is outstanding.

    Read-only, and deliberately not per-user: the queue is catalog state, the
    same for everyone. Useful for confirming that unfinished jobs are in fact
    surviving restarts.
    """
    return api_response(await catalog_sync_service.counts_by_status(db))


@router.get("/artists/info", summary="Retrieve artist profile and top tracks")
async def get_gaana_artist_info(
    seokey: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=100),
    page: int = Query(1, ge=1, le=1000)
):
    result = await catalog_service.gaana.get_artist_info([seokey], True, limit, page)
    if isinstance(result, dict) and "error" in result:
        return api_error("NOT_FOUND", result["error"], status_code=404)
    return api_response(result)


@router.get("/trending", summary="Retrieve trending songs by language")
async def get_gaana_trending(
    language: str = Query("English", max_length=50),
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    songs = await catalog_service.get_trending(db, language, limit)
    data = [
        {
            "id": s.id,
            "external_id": s.external_id,
            "title": s.title,
            "artist_name": s.artist_name,
            "album_name": s.album_name,
            "duration": s.duration,
            "thumbnail_url": s.thumbnail_url,
            "audio_url": s.audio_url,
            "stream_urls": s.stream_urls,
            "language": s.language
        }
        for s in songs
    ]
    return api_response(data)


@router.get("/newreleases", summary="Retrieve new releases")
async def get_gaana_new_releases(
    language: str = Query("English", max_length=50),
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    songs = await catalog_service.get_new_releases(db, language, limit)
    data = [
        {
            "id": s.id,
            "external_id": s.external_id,
            "title": s.title,
            "artist_name": s.artist_name,
            "album_name": s.album_name,
            "duration": s.duration,
            "thumbnail_url": s.thumbnail_url,
            "audio_url": s.audio_url,
            "stream_urls": s.stream_urls,
            "language": s.language
        }
        for s in songs
    ]
    return api_response(data)


@router.get("/charts", summary="Retrieve top charts")
async def get_gaana_charts(
    limit: int = Query(10, ge=1, le=50)
):
    result = await catalog_service.gaana.get_charts(limit)
    if isinstance(result, dict) and "error" in result:
        return api_error("NOT_FOUND", result["error"], status_code=404)
    return api_response(result)
