from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.services.catalog_service import catalog_service
from app.utils.response import api_response, api_error

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
    seokey: str = Query(
        ..., min_length=1, max_length=200,
        description="Gaana album seokey, our album uuid, or Gaana numeric "
                    "album_id -- whichever the client happens to hold.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Album detail and tracks, always fetched live from Gaana.

    The parameter keeps its name for compatibility but no longer has to *be* a
    seokey. A song payload carries `album_id` (our uuid) and nothing else about
    its album, so requiring a seokey here made "open this song album"
    impossible from every screen that lists songs. Resolution is one indexed
    lookup; see `catalog_service.resolve_album_seokey`.
    """
    resolved = await catalog_service.resolve_album_seokey(db, seokey)
    if not resolved:
        return api_error(
            "NOT_FOUND",
            "This track is not part of an album we can open.",
            status_code=404,
        )
    result = await catalog_service.gaana.get_album_info([resolved], True)
    if isinstance(result, dict) and "error" in result:
        return api_error("NOT_FOUND", result["error"], status_code=404)
    return api_response(result)


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
