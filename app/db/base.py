import uuid
from datetime import datetime
from typing import Any, Optional
from sqlalchemy import String, TypeDecorator, DateTime, func, JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB as PG_JSONB
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def as_uuid(value: Any) -> Optional[uuid.UUID]:
    """Parse an identifier into a UUID, or return None if it is not one.

    Identifiers reaching the service layer are plain strings from URL paths and
    request bodies, and they are not all UUIDs -- Gaana keys like
    "simtaangaran" are matched against Song.external_id. On PostgreSQL a GUID
    column is a native `uuid`, so binding an unparseable string raises at bind
    time and surfaces as a 500 rather than a 404. Any lookup that compares
    client input against a GUID column must screen it through here first and
    treat None as "cannot match any row".

    SQLite stores GUIDs as String(36) and compares them as text, which is why
    this whole class of bug is invisible unless the suite runs on PostgreSQL
    (see TEST_DATABASE_URL in tests/conftest.py).
    """
    if isinstance(value, uuid.UUID):
        return value
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def is_uuid(value: Any) -> bool:
    """True if `value` could name a row in a GUID column."""
    return as_uuid(value) is not None


class GUID(TypeDecorator):
    """Platform-independent GUID/UUID type.
    Uses PostgreSQL's native UUID type, otherwise uses String(36).
    """
    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == "postgresql":
            parsed = as_uuid(value)
            if parsed is None:
                # Reached only if a caller skipped the as_uuid()/is_uuid()
                # screen above. Name the fix in the message, because the
                # driver's own "badly formed hexadecimal UUID string" gives no
                # hint about which column or which caller was at fault.
                raise ValueError(
                    f"{value!r} is not a valid UUID and cannot be compared "
                    "against a uuid column. Screen client-supplied identifiers "
                    "with app.db.base.as_uuid() and return 404 instead."
                )
            return parsed
        else:
            return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return str(value)
        return str(value)


class UniversalJSON(TypeDecorator):
    """Platform-independent JSON type.
    Uses PostgreSQL JSONB if available, otherwise generic JSON.
    """
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_JSONB())
        else:
            return dialect.type_descriptor(JSON())


class Base(AsyncAttrs, DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
