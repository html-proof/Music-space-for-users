"""Offline downloads.

The backend never touches audio bytes: it authorizes a download by handing
back the stream URL for the requested quality and tracks per-device state
(queued/downloading/paused/completed/failed, progress, size) so a client can
resume, retry, and report "what's downloaded" across app reinstalls or a
second device. Actually fetching and storing the file is entirely client-side.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.models.download import Download
from app.models.song import Song
from app.models.user import User
from app.schemas.download import DownloadCreateRequest, DownloadProgressUpdate
from app.services.download_service import download_service
from app.utils.response import api_error, api_response

router = APIRouter(prefix="/api/downloads", tags=["Downloads"])


def _serialize(download: Download) -> dict:
    song: Optional[Song] = download.song
    return {
        "id": download.id,
        "song_id": download.song_id,
        "title": song.title if song else "",
        "artist_name": song.artist_name if song else "",
        "thumbnail_url": song.thumbnail_url if song else None,
        "duration": song.duration if song else 0,
        "device_id": download.device_id,
        "status": download.status,
        "quality": download.quality,
        "progress_percent": download.progress_percent,
        "file_size_bytes": download.file_size_bytes,
        "audio_url": download_service.resolve_audio_url(song, download.quality) if song else None,
        "error_message": download.error_message,
        "requested_at": download.requested_at.isoformat() if download.requested_at else "",
        "completed_at": download.completed_at.isoformat() if download.completed_at else None,
    }


@router.post("", summary="Queue a song for offline download")
async def request_download(
    req: DownloadCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    download = await download_service.request_download(
        db, current_user.id, req.song_id, req.device_id, req.quality
    )
    if not download:
        return api_error("NOT_FOUND", "Song not found", status_code=404)
    return api_response(_serialize(download), status_code=201)


@router.get("", summary="List downloads for the current user")
async def list_downloads(
    device_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    downloads = await download_service.list_downloads(
        db, current_user.id, device_id=device_id, status=status, limit=limit, offset=offset
    )
    return api_response([_serialize(d) for d in downloads])


@router.get("/storage", summary="Storage usage summary for downloads")
async def get_storage_summary(
    device_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    summary = await download_service.get_storage_summary(db, current_user.id, device_id=device_id)
    return api_response(summary)


@router.patch("/{download_id}", summary="Report download progress or status")
async def update_download(
    download_id: str,
    req: DownloadProgressUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    download = await download_service.update_progress(
        db,
        current_user.id,
        download_id,
        status=req.status,
        progress_percent=req.progress_percent,
        file_size_bytes=req.file_size_bytes,
        error_message=req.error_message,
    )
    if not download:
        return api_error("NOT_FOUND", "Download not found", status_code=404)
    return api_response(_serialize(download))


@router.delete("", summary="Remove all downloads for the current user")
async def delete_all_downloads(
    device_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    count = await download_service.delete_all_downloads(db, current_user.id, device_id=device_id)
    return api_response({"deleted": count})


@router.delete("/{download_id}", summary="Remove a single download")
async def delete_download(
    download_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    deleted = await download_service.delete_download(db, current_user.id, download_id)
    if not deleted:
        return api_error("NOT_FOUND", "Download not found", status_code=404)
    return api_response({"message": "Download removed"})
