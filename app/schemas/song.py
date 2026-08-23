from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, ConfigDict, Field


class StreamUrls(BaseModel):
    very_high_quality: Optional[str] = ""
    high_quality: Optional[str] = ""
    medium_quality: Optional[str] = ""
    low_quality: Optional[str] = ""


class SongResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_id: str
    title: str
    artist_id: Optional[str] = None
    artist_name: str = ""
    album_id: Optional[str] = None
    album_name: str = ""
    duration: int = 0
    thumbnail_url: Optional[str] = None
    audio_url: Optional[str] = None
    stream_urls: Dict[str, Any] = Field(default_factory=dict)
    source: str = "gaana"
    language: Optional[str] = None
    genre: Optional[str] = None
    mood: Optional[str] = None
    is_explicit: bool = False
    play_count: int = 0
    is_liked: Optional[bool] = False
    created_at: Optional[datetime] = None


class ArtistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_id: str
    name: str
    seokey: Optional[str] = None
    image_url: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    song_count: int = 0
    album_count: int = 0
    is_followed: Optional[bool] = False


class AlbumResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_id: str
    title: str
    seokey: Optional[str] = None
    artist_id: Optional[str] = None
    artist_name: str = ""
    cover_url: Optional[str] = None
    release_date: Optional[str] = None
    language: Optional[str] = None
    track_count: int = 0
    is_saved: Optional[bool] = False
    tracks: Optional[List[SongResponse]] = None


class GenreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    image_url: Optional[str] = None
