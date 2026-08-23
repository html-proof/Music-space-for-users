import uuid
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, GUID, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.song import Song


class Playlist(Base, TimestampMixin):
    __tablename__ = "playlists"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_collaborative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cover_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="playlists")
    songs: Mapped[List["PlaylistSong"]] = relationship(
        "PlaylistSong",
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistSong.position",
        lazy="selectin"
    )


class PlaylistSong(Base):
    __tablename__ = "playlist_songs"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    playlist_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("playlists.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    song_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("songs.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        nullable=False
    )

    __table_args__ = (
        Index("ix_playlist_position", "playlist_id", "position"),
        UniqueConstraint("playlist_id", "song_id", name="uq_playlist_song"),
    )

    playlist: Mapped["Playlist"] = relationship("Playlist", back_populates="songs")
    song: Mapped["Song"] = relationship("Song", back_populates="playlist_entries", lazy="selectin")
