import uuid
from typing import Optional
from sqlalchemy import String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, GUID, UniversalJSON, TimestampMixin


class Lyrics(Base, TimestampMixin):
    __tablename__ = "lyrics"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    song_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("songs.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    plain_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # List of {"time_ms": int, "text": str}, ordered by time_ms. Empty/None means
    # only plain lyrics are available -- the player falls back to unsynced display.
    synced_lines: Mapped[Optional[list]] = mapped_column(UniversalJSON(), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source: Mapped[str] = mapped_column(String(100), default="manual", nullable=False)
