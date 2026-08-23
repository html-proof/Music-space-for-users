from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator
from app.schemas.song import SongResponse
from app.schemas.device import DeviceResponse

PLAYBACK_STATES = ("playing", "paused", "stopped", "buffering")
REPEAT_MODES = ("off", "all", "one")
RADIO_SEED_TYPES = ("song", "artist", "mood", "personalized")
PLAYBACK_EVENTS = (
    "PLAY", "PAUSE", "RESUME", "SEEK", "SKIP", "NEXT", "PREVIOUS",
    "STOP", "BUFFER_START", "BUFFER_END", "COMPLETE",
)

_STATE_PATTERN = f"^({'|'.join(PLAYBACK_STATES)})$"
_REPEAT_PATTERN = f"^({'|'.join(REPEAT_MODES)})$"
_RADIO_SEED_PATTERN = f"^({'|'.join(RADIO_SEED_TYPES)})$"


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
    repeat_mode: str = Field(..., pattern=_REPEAT_PATTERN)
    device_id: Optional[str] = None


class RadioStartRequest(BaseModel):
    """Start an endless station. `personalized` is the only seedless type."""

    seed_type: str = Field(
        "personalized",
        pattern=_RADIO_SEED_PATTERN,
        description=", ".join(RADIO_SEED_TYPES),
    )
    seed_id: Optional[str] = Field(
        None,
        description="Song id/seokey, artist id/name, or mood name, per seed_type",
    )
    device_id: Optional[str] = None

    @model_validator(mode="after")
    def seed_id_required_unless_personalized(self) -> "RadioStartRequest":
        if self.seed_type != "personalized" and not (self.seed_id or "").strip():
            raise ValueError(f"seed_id is required when seed_type is {self.seed_type!r}")
        return self


class SyncPlaybackRequest(BaseModel):
    device_id: str
    song_id: Optional[str] = None
    playlist_id: Optional[str] = None
    position_seconds: float = Field(0.0, ge=0.0)
    duration_seconds: float = Field(0.0, ge=0.0)
    state: str = Field("playing", pattern=_STATE_PATTERN)
    volume: int = Field(100, ge=0, le=100)
    shuffle: bool = False
    repeat_mode: str = Field("off", pattern=_REPEAT_PATTERN)
    queue: Optional[List[str]] = None


class PlaybackEventRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str
    song_id: str
    # `event_type` is accepted as an alias so callers using the same wording as
    # the stored PlaybackEvent column keep working.
    event: str = Field(
        ...,
        validation_alias=AliasChoices("event", "event_type"),
        description=", ".join(PLAYBACK_EVENTS),
    )
    position: float = Field(
        0.0,
        ge=0.0,
        validation_alias=AliasChoices("position", "position_seconds"),
        description="Playback position in seconds",
    )
    duration: float = Field(
        0.0,
        ge=0.0,
        validation_alias=AliasChoices("duration", "duration_seconds"),
        description="Total song duration in seconds",
    )
    session_id: Optional[str] = None
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("event")
    @classmethod
    def known_event(cls, v: str) -> str:
        normalized = (v or "").strip().upper()
        if normalized not in PLAYBACK_EVENTS:
            raise ValueError(f"event must be one of: {', '.join(PLAYBACK_EVENTS)}")
        return normalized


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
