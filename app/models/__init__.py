from app.db.base import Base
from app.models.user import User, UserPreferences
from app.models.device import Device, UserSession
from app.models.song import Song, Artist, Album, Genre, LikedSong, SavedAlbum, FollowedArtist
from app.models.playlist import Playlist, PlaylistSong
from app.models.playback import CurrentPlayback, PlaybackEvent
from app.models.history import ListeningHistory, SearchHistory
from app.models.recommendation import UserBehaviorProfile, RecommendationSignal
from app.models.ml import MLModel
from app.models.lyrics import Lyrics
from app.models.download import Download
from app.models.onboarding import OnboardingState

__all__ = [
    "Base",
    "User",
    "UserPreferences",
    "Device",
    "UserSession",
    "Song",
    "Artist",
    "Album",
    "Genre",
    "LikedSong",
    "SavedAlbum",
    "FollowedArtist",
    "Playlist",
    "PlaylistSong",
    "CurrentPlayback",
    "PlaybackEvent",
    "ListeningHistory",
    "SearchHistory",
    "UserBehaviorProfile",
    "RecommendationSignal",
    "MLModel",
    "Lyrics",
    "Download",
    "OnboardingState",
]
