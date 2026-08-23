import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import String, Boolean, Integer, Float, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, GUID, UniversalJSON

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.song import Song


class CurrentPlayback(Base):
    __tablename__ = "current_playback"

    user_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False
    )
    device_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    song_id: Mapped[Optional[str]] = mapped_column(
        GUID(),
        ForeignKey("songs.id", ondelete="SET NULL"),
        nullable=True
    )
    playlist_id: Mapped[Optional[str]] = mapped_column(GUID(), nullable=True)
    position_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    state: Mapped[str] = mapped_column(String(50), default="stopped", nullable=False)  # playing, paused, stopped, buffering
    volume: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    shuffle: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    repeat_mode: Mapped[str] = mapped_column(String(20), default="off", nullable=False)  # off, all, one
    queue: Mapped[list] = mapped_column(UniversalJSON(), default=list, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="current_playback")
    song: Mapped[Optional["Song"]] = relationship("Song", lazy="selectin")


class PlaybackEvent(Base):
    __tablename__ = "playback_events"

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
    device_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    song_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("songs.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    # PLAY, PAUSE, RESUME, SEEK, SKIP, NEXT, PREVIOUS, STOP, BUFFER_START, BUFFER_END, COMPLETE
    position_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        index=True,
        nullable=False
    )
    event_metadata: Mapped[dict] = mapped_column(UniversalJSON(), default=dict, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="playback_events")
    song: Mapped["Song"] = relationship("Song", lazy="selectin")
