"""The durable catalog synchronization queue.

One row per "this album/song still needs to be fetched from Gaana and stored".
The queue lives in the database, not in process memory, so it survives a
restart, a force close, a crash mid-fetch, and a network outage: work that was
accepted is still there to finish afterwards.

The status values are the whole contract:

    PENDING     queued, waiting for a worker (retries come back here, with
                next_retry_at set)
    PROCESSING  claimed by a worker; reclaimed as PENDING if that worker died
    COMPLETED   the row is actually in the catalog, transaction committed
    FAILED      out of attempts; kept, never silently dropped
    ARCHIVED    completed long enough ago to be eligible for deletion

Only ARCHIVED rows are ever deleted. Nothing removes a job because a new
request arrived, a different album was opened, the player moved on, or the
worker woke up again -- an unfinished job stays until it completes or exhausts
its attempts.
"""
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, GUID, TimestampMixin

# Statuses
PENDING = "PENDING"
PROCESSING = "PROCESSING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
ARCHIVED = "ARCHIVED"

#: A job in one of these is unfinished: it must never be deleted, and a second
#: request for the same entity must reuse it rather than create a duplicate.
ACTIVE_STATUSES = (PENDING, PROCESSING)

# Entity types
ALBUM = "album"
SONG = "song"

# Priority bands. Higher runs first.
PRIORITY_PLAYING = 100      # the song the user is listening to right now
PRIORITY_NEXT = 90          # the next song in their queue
PRIORITY_ALBUM_TRACK = 70   # tracks of an album that was just opened
PRIORITY_REQUESTED = 40     # anything else a request explicitly asked for
PRIORITY_BACKGROUND = 10    # bulk catalog fill from search/feed results


class CatalogSyncJob(Base, TimestampMixin):
    __tablename__ = "catalog_sync_jobs"

    id: Mapped[str] = mapped_column(
        GUID(), primary_key=True, default=lambda: str(uuid.uuid4()), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The local row this job is about, once it exists. Set at enqueue time when
    #: the id was already handed to a client, so a crash before the write is
    #: recoverable: the worker fetches the entity and stores it under this id
    #: rather than minting a new one and orphaning what the client holds.
    entity_id: Mapped[Optional[str]] = mapped_column(GUID(), nullable=True)
    #: Gaana's own identifier -- seokey or numeric id. The idempotency key.
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=PENDING, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=PRIORITY_BACKGROUND, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    #: An album job spawns one job per track. The link records where they came
    #: from; it is not a lifetime. Deleting is ON DELETE SET NULL precisely so
    #: that archiving a finished album job can never cascade into its unfinished
    #: track jobs.
    parent_job_id: Mapped[Optional[str]] = mapped_column(
        GUID(), ForeignKey("catalog_sync_jobs.id", ondelete="SET NULL"), nullable=True
    )

    parent: Mapped[Optional["CatalogSyncJob"]] = relationship(
        "CatalogSyncJob", back_populates="children", remote_side=[id]
    )
    children: Mapped[List["CatalogSyncJob"]] = relationship(
        "CatalogSyncJob", back_populates="parent"
    )

    __table_args__ = (
        # Idempotency (requirement: never two open jobs for the same entity).
        # Partial, so that a COMPLETED job does not block a later re-sync of the
        # same album -- only unfinished ones are exclusive. Postgres and SQLite
        # both support partial indexes; they are the only two dialects here.
        Index(
            "ix_catalog_sync_jobs_active_entity",
            "entity_type",
            "external_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING','PROCESSING')"),
            sqlite_where=text("status IN ('PENDING','PROCESSING')"),
        ),
        # The claim query: highest priority first among what is due.
        Index("ix_catalog_sync_jobs_claim", "status", "priority", "next_retry_at"),
        Index("ix_catalog_sync_jobs_entity", "entity_type", "external_id"),
    )
