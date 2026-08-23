import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, GUID, TimestampMixin

# Status lifecycle: queued -> downloading -> completed, with paused/failed as
# side branches. failed can be retried by resetting to queued.
DOWNLOAD_STATUSES = ("queued", "downloading", "paused", "completed", "failed")
# Matches the keys already present in Song.stream_urls (see catalog_service).
DOWNLOAD_QUALITIES = ("low_quality", "medium_quality", "high_quality", "very_high_quality")


class Download(Base, TimestampMixin):
    """A user's explicit request to keep a song available offline on one device.

    This is the permanent-download half of the offline system, distinct from
    any client-side temporary playback cache: a row here persists until the
    user or client removes it, and exists so a re-installed or second device
    can learn what should be re-fetched, and so storage usage can be reported
    back to the user without the backend ever touching the audio bytes
    themselves -- those move directly from the stream URL to the device.
    """
    __tablename__ = "downloads"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    song_id: Mapped[str] = mapped_column(GUID(), ForeignKey("songs.id", ondelete="CASCADE"), index=True, nullable=False)
    device_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    quality: Mapped[str] = mapped_column(String(20), default="high_quality", nullable=False)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_user_song_device_download", "user_id", "song_id", "device_id", unique=True),
    )

    song: Mapped["Song"] = relationship("Song", lazy="selectin")
