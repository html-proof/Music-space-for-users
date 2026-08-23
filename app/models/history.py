import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import String, Boolean, Float, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, GUID, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.song import Song


class ListeningHistory(Base):
    __tablename__ = "listening_history"

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
    song_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("songs.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    device_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        nullable=False
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_listened: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    completion_percentage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="direct", nullable=False)

    __table_args__ = (
        Index("ix_history_user_started", "user_id", "started_at"),
    )

    user: Mapped["User"] = relationship("User", back_populates="listening_history")
    song: Mapped["Song"] = relationship("Song", lazy="selectin")


class SearchHistory(Base):
    __tablename__ = "search_history"

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
    query: Mapped[str] = mapped_column(String(512), nullable=False)
    result_type: Mapped[str] = mapped_column(String(50), default="all", nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        index=True,
        nullable=False
    )

    __table_args__ = (
        Index("ix_search_user_time", "user_id", "timestamp"),
    )

    user: Mapped["User"] = relationship("User", back_populates="search_history")
