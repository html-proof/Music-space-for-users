from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.download import DOWNLOAD_QUALITIES, DOWNLOAD_STATUSES


class DownloadCreateRequest(BaseModel):
    song_id: str = Field(..., min_length=1)
    device_id: str = Field(..., min_length=1, max_length=255)
    quality: str = Field("high_quality", description=f"One of {DOWNLOAD_QUALITIES}")


class DownloadProgressUpdate(BaseModel):
    status: Optional[str] = Field(None, description=f"One of {DOWNLOAD_STATUSES}")
    progress_percent: Optional[int] = Field(None, ge=0, le=100)
    file_size_bytes: Optional[int] = Field(None, ge=0)
    error_message: Optional[str] = Field(None, max_length=500)


class DownloadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    song_id: str
    title: str
    artist_name: str
    thumbnail_url: Optional[str] = None
    duration: int = 0
    device_id: str
    status: str
    quality: str
    progress_percent: int
    file_size_bytes: Optional[int] = None
    audio_url: Optional[str] = None
    error_message: Optional[str] = None
    requested_at: datetime
    completed_at: Optional[datetime] = None


class DownloadStorageSummary(BaseModel):
    total_downloads: int
    completed_downloads: int
    total_bytes: int
    by_status: dict
