import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, GUID, TimestampMixin


class OnboardingState(Base, TimestampMixin):
    """Whether a user has finished the first-run language/artist walkthrough.

    Kept as its own table rather than a new column on `user_preferences` so it
    is created the same way as every other new table here -- `create_all` on
    startup -- with no ALTER TABLE required against an already-deployed
    database (this project has no Alembic migrations wired up).
    """
    __tablename__ = "onboarding_state"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
