import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Boolean, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, GUID, UniversalJSON


class MLModel(Base):
    """A versioned, serialised model artifact.

    Artifacts live in Postgres rather than on disk because Render's filesystem is
    ephemeral and not shared between instances -- a model trained in a background
    task would otherwise vanish on the next deploy and differ per instance.

    Everything stored here is deliberately small:
      - "ranker"     -> ~25 coefficients plus a bias and metrics
      - "item_sim"   -> top-K neighbours, only for songs that have interactions
      - "popularity" -> per-song completion/skip aggregates for the same subset
    Song content vectors are *not* stored: they are a pure function of the song
    row (see app/ml/features.py), so persisting them would add a table to keep in
    sync for no benefit.

    Exactly one row per `name` should have is_active=True. Older rows are kept so
    a bad promotion can be rolled back and so metrics history is inspectable.
    """
    __tablename__ = "ml_models"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), index=True, nullable=False)  # ranker, item_sim, popularity
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    artifact: Mapped[dict] = mapped_column(UniversalJSON(), default=dict, nullable=False)
    metrics: Mapped[dict] = mapped_column(UniversalJSON(), default=dict, nullable=False)
    n_samples: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    n_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    # Why this artifact was or was not promoted, so `GET /api/ml/status` can
    # explain "still serving prior weights" without anyone reading the logs.
    notes: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        index=True,
        nullable=False
    )

    __table_args__ = (
        Index("ix_ml_models_name_active", "name", "is_active"),
        Index("ix_ml_models_name_version", "name", "version", unique=True),
    )
