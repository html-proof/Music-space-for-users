from app.schemas.common import PaginationParams, PaginatedResponse, MessageResponse
from app.schemas.auth import UserProfileResponse, SyncUserRequest
from app.schemas.user import UserPreferencesResponse, UserPreferencesUpdate, UserAnalyticsResponse
from app.schemas.device import DeviceRegisterRequest, DeviceHeartbeatRequest, DeviceResponse, UserSessionResponse
from app.schemas.song import SongResponse, ArtistResponse, AlbumResponse, GenreResponse, StreamUrls
from app.schemas.playlist import PlaylistCreate, PlaylistUpdate, PlaylistResponse, PlaylistSongItem, AddPlaylistSongRequest, ReorderPlaylistSongsRequest
from app.schemas.playback import PlayRequest, PauseRequest, ResumeRequest, SeekRequest, VolumeRequest, ShuffleRequest, RepeatRequest, SyncPlaybackRequest, PlaybackEventRequest, PlaybackStateResponse
from app.schemas.history import ListeningHistoryResponse, SearchHistoryResponse, SearchLogRequest
from app.schemas.recommendation import RecommendationCategoryResponse, HomeRecommendationsResponse, SimilarArtistsResponse

__all__ = [
    "PaginationParams",
    "PaginatedResponse",
    "MessageResponse",
    "UserProfileResponse",
    "SyncUserRequest",
    "UserPreferencesResponse",
    "UserPreferencesUpdate",
    "UserAnalyticsResponse",
    "DeviceRegisterRequest",
    "DeviceHeartbeatRequest",
    "DeviceResponse",
    "UserSessionResponse",
    "SongResponse",
    "ArtistResponse",
    "AlbumResponse",
    "GenreResponse",
    "StreamUrls",
    "PlaylistCreate",
    "PlaylistUpdate",
    "PlaylistResponse",
    "PlaylistSongItem",
    "AddPlaylistSongRequest",
    "ReorderPlaylistSongsRequest",
    "PlayRequest",
    "PauseRequest",
    "ResumeRequest",
    "SeekRequest",
    "VolumeRequest",
    "ShuffleRequest",
    "RepeatRequest",
    "SyncPlaybackRequest",
    "PlaybackEventRequest",
    "PlaybackStateResponse",
    "ListeningHistoryResponse",
    "SearchHistoryResponse",
    "SearchLogRequest",
    "RecommendationCategoryResponse",
    "HomeRecommendationsResponse",
    "SimilarArtistsResponse",
]
