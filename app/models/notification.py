import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, GUID, TimestampMixin


class NotificationPreferences(Base, TimestampMixin):
    __tablename__ = "notification_preferences"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    # On by default: a followed artist's new release is the notification the
    # user explicitly opted into by following them in the first place.
    new_release_songs: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Notification(Base):
    """A push (or in-app-only, if the user has no registered device) notification.

    Kept as a durable log rather than fire-and-forget: it is the "why did I
    get this" record, the in-app notification inbox, and -- since a song is
    only ever discovered as new once (see ReleaseWatchService) -- the natural
    place a client reads from rather than requiring its own push-history
    store.
    """
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # "new_release_song"
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(String(1024), nullable=False)
    song_id: Mapped[Optional[str]] = mapped_column(GUID(), ForeignKey("songs.id", ondelete="SET NULL"), nullable=True)
    artist_id: Mapped[Optional[str]] = mapped_column(GUID(), ForeignKey("artists.id", ondelete="SET NULL"), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), nullable=False
    )
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
