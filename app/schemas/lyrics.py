from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SyncedLine(BaseModel):
    time_ms: int = Field(..., ge=0)
    text: str


class LyricsUpsertRequest(BaseModel):
    plain_text: Optional[str] = None
    synced_lines: Optional[List[SyncedLine]] = None
    language: Optional[str] = Field(None, max_length=50)
    source: str = Field("manual", max_length=100)


class LyricsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    song_id: str
    has_lyrics: bool
    is_synced: bool
    plain_text: Optional[str] = None
    synced_lines: Optional[List[SyncedLine]] = None
    language: Optional[str] = None
    source: Optional[str] = None
