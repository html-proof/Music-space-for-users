from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.song import SongResponse


class PlaylistCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1024)
    is_public: bool = False
    is_collaborative: bool = False
    cover_url: Optional[str] = None


class PlaylistUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1024)
    is_public: Optional[bool] = None
    is_collaborative: Optional[bool] = None
    cover_url: Optional[str] = None


class PlaylistSongItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    playlist_id: str
    song_id: str
    position: int
    added_at: datetime
    song: Optional[SongResponse] = None


class PlaylistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    description: Optional[str] = None
    is_public: bool
    is_collaborative: bool
    cover_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    song_count: int = 0
    songs: Optional[List[PlaylistSongItem]] = None


class AddPlaylistSongRequest(BaseModel):
    song_id: str
    position: Optional[int] = None


class ReorderPlaylistSongsRequest(BaseModel):
    song_ids: List[str] = Field(..., description="Ordered list of song IDs representing the new playlist sequence")
