import uuid
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, GUID, UniversalJSON, TimestampMixin

if TYPE_CHECKING:
    from app.models.device import Device, UserSession
    from app.models.playlist import Playlist
    from app.models.playback import CurrentPlayback, PlaybackEvent
    from app.models.history import ListeningHistory, SearchHistory
    from app.models.song import LikedSong, SavedAlbum, FollowedArtist
    from app.models.recommendation import UserBehaviorProfile


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    firebase_uid: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=False
    )
    email: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    photo_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(10), default="IN", nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(50), default="English", nullable=True)
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        nullable=False
    )

    # Relationships
    preferences: Mapped[Optional["UserPreferences"]] = relationship(
        "UserPreferences",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    devices: Mapped[List["Device"]] = relationship(
        "Device",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    sessions: Mapped[List["UserSession"]] = relationship(
        "UserSession",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    playlists: Mapped[List["Playlist"]] = relationship(
        "Playlist",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    current_playback: Mapped[Optional["CurrentPlayback"]] = relationship(
        "CurrentPlayback",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    playback_events: Mapped[List["PlaybackEvent"]] = relationship(
        "PlaybackEvent",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    listening_history: Mapped[List["ListeningHistory"]] = relationship(
        "ListeningHistory",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    search_history: Mapped[List["SearchHistory"]] = relationship(
        "SearchHistory",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    liked_songs: Mapped[List["LikedSong"]] = relationship(
        "LikedSong",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    saved_albums: Mapped[List["SavedAlbum"]] = relationship(
        "SavedAlbum",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    followed_artists: Mapped[List["FollowedArtist"]] = relationship(
        "FollowedArtist",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    behavior_profile: Mapped[Optional["UserBehaviorProfile"]] = relationship(
        "UserBehaviorProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )


class UserPreferences(Base, TimestampMixin):
    __tablename__ = "user_preferences"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False
    )
    preferred_languages: Mapped[list] = mapped_column(
        UniversalJSON(),
        default=lambda: ["English", "Hindi"],
        nullable=False
    )
    favorite_artists: Mapped[list] = mapped_column(
        UniversalJSON(),
        default=list,
        nullable=False
    )
    favorite_genres: Mapped[list] = mapped_column(
        UniversalJSON(),
        default=list,
        nullable=False
    )
    favorite_moods: Mapped[list] = mapped_column(
        UniversalJSON(),
        default=list,
        nullable=False
    )
    explicit_content: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    audio_quality: Mapped[str] = mapped_column(String(50), default="very_high", nullable=False)
    autoplay: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    crossfade: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    data_saver: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="preferences")
