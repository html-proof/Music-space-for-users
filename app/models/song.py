import uuid
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, GUID, UniversalJSON, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.playlist import PlaylistSong


class Song(Base, TimestampMixin):
    __tablename__ = "songs"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    artist_id: Mapped[Optional[str]] = mapped_column(GUID(), ForeignKey("artists.id", ondelete="SET NULL"), index=True, nullable=True)
    artist_name: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    album_id: Mapped[Optional[str]] = mapped_column(GUID(), ForeignKey("albums.id", ondelete="SET NULL"), index=True, nullable=True)
    album_name: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    duration: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # in seconds
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    audio_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    stream_urls: Mapped[dict] = mapped_column(UniversalJSON(), default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="gaana", nullable=False)
    language: Mapped[Optional[str]] = mapped_column(String(50), index=True, nullable=True)
    genre: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    mood: Mapped[Optional[str]] = mapped_column(String(50), index=True, nullable=True)
    is_explicit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    play_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    artist: Mapped[Optional["Artist"]] = relationship("Artist", back_populates="songs")
    album: Mapped[Optional["Album"]] = relationship("Album", back_populates="songs")
    playlist_entries: Mapped[List["PlaylistSong"]] = relationship("PlaylistSong", back_populates="song", cascade="all, delete-orphan")


class Artist(Base, TimestampMixin):
    __tablename__ = "artists"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    seokey: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    genres: Mapped[list] = mapped_column(UniversalJSON(), default=list, nullable=False)
    song_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    album_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    songs: Mapped[List["Song"]] = relationship("Song", back_populates="artist")
    albums: Mapped[List["Album"]] = relationship("Album", back_populates="artist")


class Album(Base, TimestampMixin):
    __tablename__ = "albums"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    seokey: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    artist_id: Mapped[Optional[str]] = mapped_column(GUID(), ForeignKey("artists.id", ondelete="SET NULL"), index=True, nullable=True)
    artist_name: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    cover_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    release_date: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    track_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    artist: Mapped[Optional["Artist"]] = relationship("Artist", back_populates="albums")
    songs: Mapped[List["Song"]] = relationship("Song", back_populates="album")


class Genre(Base):
    __tablename__ = "genres"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)


class LikedSong(Base):
    __tablename__ = "liked_songs"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    song_id: Mapped[str] = mapped_column(GUID(), ForeignKey("songs.id", ondelete="CASCADE"), index=True, nullable=False)
    liked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_user_song_liked", "user_id", "song_id", unique=True),
    )

    user: Mapped["User"] = relationship("User", back_populates="liked_songs")
    song: Mapped["Song"] = relationship("Song", lazy="selectin")


class SavedAlbum(Base):
    __tablename__ = "saved_albums"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    album_id: Mapped[str] = mapped_column(GUID(), ForeignKey("albums.id", ondelete="CASCADE"), index=True, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_user_album_saved", "user_id", "album_id", unique=True),
    )

    user: Mapped["User"] = relationship("User", back_populates="saved_albums")
    album: Mapped["Album"] = relationship("Album", lazy="selectin")


class FollowedArtist(Base):
    __tablename__ = "followed_artists"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    artist_id: Mapped[str] = mapped_column(GUID(), ForeignKey("artists.id", ondelete="CASCADE"), index=True, nullable=False)
    followed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_user_artist_followed", "user_id", "artist_id", unique=True),
    )

    user: Mapped["User"] = relationship("User", back_populates="followed_artists")
    artist: Mapped["Artist"] = relationship("Artist", lazy="selectin")
