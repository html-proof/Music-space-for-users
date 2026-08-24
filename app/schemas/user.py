from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class UserPreferencesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # No default. A user who has not chosen has chosen nothing, and claiming
    # otherwise here would report a preference they never expressed -- the
    # stored column already defaults to an empty list, so this only ever
    # disagreed with it.
    preferred_languages: List[str] = Field(default_factory=list)
    favorite_artists: List[str] = Field(default_factory=list)
    favorite_genres: List[str] = Field(default_factory=list)
    favorite_moods: List[str] = Field(default_factory=list)
    explicit_content: bool = True
    audio_quality: str = "very_high"  # very_high, high, medium, low
    autoplay: bool = True
    crossfade: int = 0  # 0 to 12 seconds
    data_saver: bool = False
    headset_safety_reminder: bool = True


class UserPreferencesUpdate(BaseModel):
    preferred_languages: Optional[List[str]] = None
    favorite_artists: Optional[List[str]] = None
    favorite_genres: Optional[List[str]] = None
    favorite_moods: Optional[List[str]] = None
    explicit_content: Optional[bool] = None
    audio_quality: Optional[str] = None
    autoplay: Optional[bool] = None
    crossfade: Optional[int] = Field(None, ge=0, le=12)
    data_saver: Optional[bool] = None
    headset_safety_reminder: Optional[bool] = None


class UserAnalyticsResponse(BaseModel):
    top_artists: List[dict] = Field(default_factory=list)
    top_genres: List[dict] = Field(default_factory=list)
    top_languages: List[dict] = Field(default_factory=list)
    top_moods: List[dict] = Field(default_factory=list)
    average_session_minutes: float = 0.0
    completion_rate: float = 0.0
    skip_rate: float = 0.0
