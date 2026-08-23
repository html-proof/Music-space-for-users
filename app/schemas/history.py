from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict
from app.schemas.song import SongResponse


class ListeningHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    song_id: str
    device_id: Optional[str] = None
    session_id: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_listened: float
    completion_percentage: float
    skipped: bool
    source: str
    song: Optional[SongResponse] = None


class SearchHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    query: str
    result_type: str
    timestamp: datetime


class SearchLogRequest(BaseModel):
    query: str
    result_type: Optional[str] = "all"
