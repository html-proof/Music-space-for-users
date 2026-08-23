import uuid
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, GUID


class Language(Base):
    """The selectable-language catalog for onboarding.

    Exists so the Flutter client never hardcodes a language list --
    GET /api/onboarding/languages is the single source of truth, seeded once
    at startup (see app/db/init_db.py) and editable in the database without a
    client release.
    """
    __tablename__ = "languages"

    id: Mapped[str] = mapped_column(
        GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
