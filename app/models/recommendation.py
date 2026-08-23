import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import String, Float, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, GUID, UniversalJSON

if TYPE_CHECKING:
    from app.models.user import User


class UserBehaviorProfile(Base):
    __tablename__ = "user_behavior_profiles"

    user_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False
    )
    top_artists: Mapped[list] = mapped_column(UniversalJSON(), default=list, nullable=False)
    top_genres: Mapped[list] = mapped_column(UniversalJSON(), default=list, nullable=False)
    top_languages: Mapped[list] = mapped_column(UniversalJSON(), default=list, nullable=False)
    top_moods: Mapped[list] = mapped_column(UniversalJSON(), default=list, nullable=False)
    average_session_minutes: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    completion_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    skip_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    affinity_scores: Mapped[dict] = mapped_column(UniversalJSON(), default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="behavior_profile")


class RecommendationSignal(Base):
    __tablename__ = "recommendation_signals"

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
    entity_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)  # artist, genre, language, mood, song
    entity_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    signal_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)  # play, like, skip, repeat, playlist_add
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        index=True,
        nullable=False
    )

    __table_args__ = (
        Index("ix_rec_user_entity", "user_id", "entity_type", "entity_id"),
    )
