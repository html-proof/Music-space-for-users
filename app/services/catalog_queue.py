"""In-process write queue for the Gaana catalog mirror.

Every Gaana track a search or home shelf touches used to be written straight
through inside the request: a SELECT plus possible INSERT for the artist, the
same for the album, then the song, then a COMMIT -- per track, times twenty
tracks, times every shelf. That is most of the latency of a search response,
and none of it is what the caller is waiting for. The `songs`/`albums` rows
exist only to give a Gaana track a stable local id that likes, history and
playlists can reference (see the note at the top of catalog_service); the
client needs that id, not the durability of the row, before it can render.

So the id is resolved cheaply and the row itself is queued:

  * `resolve_id` returns the id an external_id maps to -- from the in-memory
    identity map, else from a single indexed SELECT, else a freshly minted
    uuid4 -- so the id handed to the client is the id the row will have.
  * `enqueue` records the parsed payload, keyed by external_id, so twenty
    shelves touching the same track coalesce into one write.
  * `flush` drains the queue in FK order (artists, albums, songs) as one
    batched upsert per table, run by the background flusher every
    CATALOG_FLUSH_INTERVAL_SECONDS.

The gap this opens is that a queued id does not yet exist as a row, and
`liked_songs` / `playlist_songs` / `downloads` are real foreign keys to it.
`ensure_persisted` closes it: any write path about to reference an id flushes
first if that id is still pending. It is a no-op in the common case, since the
flusher has usually already run.

The queue is per-process and in memory, so an unflushed batch is lost on
restart. That is acceptable precisely because these rows are a cache of Gaana
and not user data -- the next request for the same track re-queues it -- but it
is the reason nothing user-owned may ever be routed through here.
"""
import asyncio
import logging
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import Boolean, Integer, String, Text, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import GUID, UniversalJSON
from app.models.song import Album, Artist, Song

logger = logging.getLogger("catalog_queue")

# Cap on the external_id -> uuid identity map. Dropping an entry is safe: the
# next `resolve_id` for it falls back to the indexed SELECT and finds the row
# the flusher wrote. Without a cap the map would grow with the number of
# distinct Gaana tracks the process has ever seen, which is unbounded.
IDENTITY_CACHE_MAX = 50_000

_MODELS = {"artist": Artist, "album": Album, "song": Song}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _keep_if_incoming_empty(existing, incoming):
    """`incoming` unless it is empty, in which case keep `existing`.

    The synchronous upsert this replaced wrote `song.audio_url = new or
    song.audio_url` field by field, so a sparser copy of a track -- Gaana
    returns those, notably without stream urls or an artist seokey -- refreshed
    what it knew and left the rest alone. A plain `SET col = excluded.col`
    would instead blank those columns out, which is how a playable song loses
    its stream urls the second time anyone searches for it.

    Only comparisons that survive `executemany` are usable here, which rules
    out `IN (...)`: it compiles to an expanding parameter, and the whole batch
    goes through one executemany.
    """
    column_type = existing.type
    if isinstance(column_type, UniversalJSON):
        # Empty stream_urls/genres arrive as {} or [], not NULL.
        as_text = cast(incoming, String)
        empty = or_(
            incoming.is_(None),
            as_text == "{}",
            as_text == "[]",
            as_text == "null",
            as_text == "",
        )
    elif isinstance(column_type, Boolean):
        # False is a value, not an absence.
        return incoming
    elif isinstance(column_type, GUID):
        # Checked before String: GUID is a String underneath on SQLite.
        empty = incoming.is_(None)
    elif isinstance(column_type, Integer):
        # `duration` is 0 when Gaana omitted it.
        empty = or_(incoming.is_(None), incoming == 0)
    elif isinstance(column_type, (String, Text)):
        empty = or_(incoming.is_(None), incoming == "")
    else:
        empty = incoming.is_(None)
    return case((empty, existing), else_=incoming)


class CatalogWriteQueue:
    def __init__(self):
        # external_id -> column dict, pending write. Re-enqueuing the same
        # external_id replaces the payload rather than appending, so a hot
        # track costs one row per flush however often it is seen.
        self._pending: Dict[str, Dict[str, Dict[str, Any]]] = {
            "artist": {}, "album": {}, "song": {},
        }
        self._ids: "OrderedDict[Tuple[str, str], str]" = OrderedDict()
        self._canonical: Dict[Tuple[str, str], str] = {}
        # Row ids currently unwritten, so `ensure_persisted` can answer without
        # walking the payloads.
        self._pending_ids: set = set()
        self._lock = asyncio.Lock()

    # -- identity ---------------------------------------------------------

    def _cache_put(self, kind: str, external_id: str, row_id: str, canonical: str) -> None:
        key = (kind, external_id)
        self._ids[key] = row_id
        self._canonical[key] = canonical
        self._ids.move_to_end(key)
        while len(self._ids) > IDENTITY_CACHE_MAX:
            dropped, _ = self._ids.popitem(last=False)
            self._canonical.pop(dropped, None)

    async def resolve_id(
        self,
        db: AsyncSession,
        kind: str,
        external_id: str,
        *,
        match_name: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Return `(row_id, canonical_external_id)` for an upstream key.

        `canonical_external_id` is the external_id the row actually carries. It
        differs from the one passed in when an existing row was matched by name
        instead -- artists and albums are matched the way `get_or_create_*`
        matched them, so queueing does not start creating near-duplicates of
        rows those calls would have reused. The upsert later conflicts on the
        canonical value; conflicting on the passed-in one would miss and
        collide on the primary key instead.
        """
        model = _MODELS[kind]
        key = (kind, external_id)
        if key in self._ids:
            self._ids.move_to_end(key)
            return self._ids[key], self._canonical.get(key, external_id)

        where = model.external_id == external_id
        if match_name is not None:
            name_col = Artist.name if kind == "artist" else Album.title
            where = or_(where, name_col == match_name)
        row = (await db.execute(select(model).where(where))).scalars().first()
        if row is not None:
            self._cache_put(kind, external_id, row.id, row.external_id)
            return row.id, row.external_id

        row_id = str(uuid.uuid4())
        self._cache_put(kind, external_id, row_id, external_id)
        return row_id, external_id

    # -- enqueue ----------------------------------------------------------

    async def enqueue(self, kind: str, values: Dict[str, Any]) -> None:
        async with self._lock:
            self._pending[kind][values["external_id"]] = values
            self._pending_ids.add(values["id"])

    def is_pending(self, row_id: Optional[str]) -> bool:
        return row_id is not None and row_id in self._pending_ids

    def depth(self) -> Dict[str, int]:
        return {kind: len(rows) for kind, rows in self._pending.items()}

    # -- flush ------------------------------------------------------------

    async def flush(self, db: AsyncSession) -> Dict[str, int]:
        """Write everything queued, in FK order. Returns rows written per table."""
        async with self._lock:
            batch = {kind: list(rows.values()) for kind, rows in self._pending.items()}
            self._pending = {"artist": {}, "album": {}, "song": {}}
            drained_ids = self._pending_ids
            self._pending_ids = set()

        if not any(batch.values()):
            return {kind: 0 for kind in batch}

        try:
            written = {}
            # Artists before albums before songs: each carries the previous
            # one's id as a foreign key.
            for kind in ("artist", "album", "song"):
                written[kind] = await self._upsert(db, _MODELS[kind], batch[kind])
            await db.commit()
            return written
        except Exception:
            await db.rollback()
            # These rows are a cache of Gaana, so a failed batch is dropped
            # rather than re-queued: retrying would let one poison payload fail
            # every subsequent flush, and the next request for the same track
            # enqueues it again anyway. The ids stay resolvable from the
            # identity map, so any id already handed to a client still matches
            # the row a later flush writes.
            self._pending_ids -= drained_ids
            raise

    async def _upsert(self, db: AsyncSession, model, rows: list) -> int:
        if not rows:
            return 0
        dialect = db.bind.dialect.name if db.bind is not None else "postgresql"
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as _insert
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as _insert
        else:  # pragma: no cover - the app supports no other dialect
            raise RuntimeError(f"catalog queue cannot upsert on dialect {dialect!r}")

        table = model.__table__
        stmt = _insert(table)
        # On conflict, refresh only the columns the payload actually carries,
        # minus the ones the existing row owns. That excludes `id` and
        # `created_at` (the id is already in clients' hands as an FK target),
        # and -- because they are simply absent from the payload -- every
        # locally accumulated or separately sourced field: `play_count`,
        # `album.track_count`, `artist.song_count`, and the release_date /
        # genres that only `get_album_details` and `get_artist_details` know.
        # Updating the full column list instead would reset those to the
        # defaults a search result knows nothing about.
        immutable = {"id", "external_id", "created_at", "updated_at"}
        set_ = {
            k: _keep_if_incoming_empty(table.c[k], stmt.excluded[k])
            for k in rows[0] if k not in immutable
        }
        set_["updated_at"] = func.now()
        stmt = stmt.on_conflict_do_update(index_elements=[table.c.external_id], set_=set_)
        await db.execute(stmt, rows)
        return len(rows)

    async def ensure_kind_persisted(self, db: AsyncSession, kind: str) -> bool:
        """Flush if anything of `kind` is queued.

        For the lookups that find a row by its upstream key or name rather than
        by id -- a radio seed naming an artist, a worker checking whether a
        seokey is already known. They cannot ask `is_pending` about an id they
        do not have, and a miss there does not mean the row is absent, it means
        the row may not be written yet.
        """
        if not self._pending.get(kind):
            return False
        await self.flush(db)
        return True

    async def ensure_persisted(self, db: AsyncSession, *row_ids: Optional[str]) -> bool:
        """Flush if any of `row_ids` is still queued.

        Called by the write paths that turn an id into a real foreign key
        (like, save, playlist add, history, download) so none of them can
        insert a reference to a row the flusher has not written yet.
        """
        if not any(self.is_pending(r) for r in row_ids):
            return False
        await self.flush(db)
        return True


catalog_queue = CatalogWriteQueue()
