from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class NotificationPreferencesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    new_release_songs: bool = True


class NotificationPreferencesUpdate(BaseModel):
    new_release_songs: Optional[bool] = None


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category: str
    title: str
    body: str
    song_id: Optional[str] = None
    artist_id: Optional[str] = None
    sent_at: datetime
    read_at: Optional[datetime] = None
    is_read: bool = Field(default=False)
