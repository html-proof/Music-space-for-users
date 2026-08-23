from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.song import SongResponse
from app.schemas.device import DeviceResponse


class PlayRequest(BaseModel):
    song_id: str
    playlist_id: Optional[str] = None
    device_id: Optional[str] = None
    position_seconds: float = 0.0
    queue: Optional[List[str]] = None


class PauseRequest(BaseModel):
    device_id: Optional[str] = None
    position_seconds: float = 0.0


class ResumeRequest(BaseModel):
    device_id: Optional[str] = None
    position_seconds: Optional[float] = None


class SeekRequest(BaseModel):
    position_seconds: float = Field(..., ge=0.0)
    device_id: Optional[str] = None


class VolumeRequest(BaseModel):
    volume: int = Field(..., ge=0, le=100)
    device_id: Optional[str] = None


class ShuffleRequest(BaseModel):
    shuffle: bool
    device_id: Optional[str] = None


class RepeatRequest(BaseModel):
    repeat_mode: str = Field(..., pattern="^(off|all|one)$")
    device_id: Optional[str] = None


class SyncPlaybackRequest(BaseModel):
    device_id: str
    song_id: Optional[str] = None
    playlist_id: Optional[str] = None
    position_seconds: float = 0.0
    duration_seconds: float = 0.0
    state: str = Field("playing", pattern="^(playing|paused|stopped|buffering)$")
    volume: int = 100
    shuffle: bool = False
    repeat_mode: str = "off"
    queue: Optional[List[str]] = None


class PlaybackEventRequest(BaseModel):
    device_id: str
    song_id: str
    event: str = Field(..., description="play, pause, resume, seek, skip, next, previous, stop, buffer_start, buffer_end, complete")
    position: float = Field(0.0, description="Playback position in seconds")
    duration: float = Field(0.0, description="Total song duration in seconds")
    session_id: Optional[str] = None
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class PlaybackStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    device_id: Optional[str] = None
    song_id: Optional[str] = None
    playlist_id: Optional[str] = None
    position_seconds: float = 0.0
    duration_seconds: float = 0.0
    state: str = "stopped"  # playing, paused, stopped, buffering
    volume: int = 100
    shuffle: bool = False
    repeat_mode: str = "off"  # off, all, one
    queue: List[str] = Field(default_factory=list)
    updated_at: datetime
    song: Optional[SongResponse] = None
    active_device: Optional[DeviceResponse] = None
