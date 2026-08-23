import logging
from typing import List, Optional
from sqlalchemy import select, and_, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import is_uuid
from app.models.playlist import Playlist, PlaylistSong
from app.models.song import Song
from app.schemas.playlist import PlaylistCreate, PlaylistUpdate
from app.services.signal_service import SignalService

logger = logging.getLogger("playlist_service")


class PlaylistService:
    @staticmethod
    async def create_playlist(db: AsyncSession, user_id: str, req: PlaylistCreate) -> Playlist:
        playlist = Playlist(
            user_id=user_id,
            title=req.title,
            description=req.description,
            is_public=req.is_public,
            is_collaborative=req.is_collaborative,
            cover_url=req.cover_url
        )
        db.add(playlist)
        await db.commit()
        await db.refresh(playlist)
        return playlist

    @staticmethod
    async def get_user_playlists(
        db: AsyncSession,
        user_id: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[Playlist]:
        stmt = (
            select(Playlist)
            .where(Playlist.user_id == user_id)
            .order_by(Playlist.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_playlist(
        db: AsyncSession,
        playlist_id: str,
        user_id: Optional[str] = None
    ) -> Optional[Playlist]:
        # playlist_id comes straight from the URL path. Playlist.id is a native
        # uuid on PostgreSQL, so a malformed id has to short-circuit to "not
        # found" -- binding it would raise and turn a 404 into a 500.
        if not is_uuid(playlist_id):
            return None
        stmt = select(Playlist).where(Playlist.id == playlist_id)
        res = await db.execute(stmt)
        playlist = res.scalar_one_or_none()
        if not playlist:
            return None
        # Visible if public, owned by the caller, or collaborative -- that
        # last case matters because add_song/remove_song/reorder_songs all
        # fetch through this same method to authorize a non-owner via
        # `is_collaborative`, and a check here that ignored it would make a
        # private collaborative playlist invisible (hence unmodifiable) to
        # every collaborator but the owner, contradicting the flag's purpose.
        if not playlist.is_public and not playlist.is_collaborative and playlist.user_id != user_id:
            return None
        return playlist

    @staticmethod
    async def update_playlist(
        db: AsyncSession,
        playlist_id: str,
        user_id: str,
        req: PlaylistUpdate
    ) -> Optional[Playlist]:
        if not is_uuid(playlist_id):
            return None
        stmt = select(Playlist).where(and_(Playlist.id == playlist_id, Playlist.user_id == user_id))
        res = await db.execute(stmt)
        playlist = res.scalar_one_or_none()
        if not playlist:
            return None

        update_data = req.model_dump(exclude_unset=True)
        for k, v in update_data.items():
            setattr(playlist, k, v)

        await db.commit()
        await db.refresh(playlist)
        return playlist

    @staticmethod
    async def delete_playlist(db: AsyncSession, playlist_id: str, user_id: str) -> bool:
        if not is_uuid(playlist_id):
            return False
        stmt = select(Playlist).where(and_(Playlist.id == playlist_id, Playlist.user_id == user_id))
        res = await db.execute(stmt)
        playlist = res.scalar_one_or_none()
        if not playlist:
            return False

        await db.delete(playlist)
        await db.commit()
        return True

    @staticmethod
    async def _lock_playlist(db: AsyncSession, playlist_id: str) -> None:
        """
        Serialize concurrent mutations of one playlist's ordering by taking a row
        lock on the parent playlist. Without it, two simultaneous adds both read
        the same count and land on the same position. No-op on SQLite, which
        serializes writers anyway.
        """
        if db.bind is not None and db.bind.dialect.name == "sqlite":
            return
        await db.execute(
            select(Playlist.id).where(Playlist.id == playlist_id).with_for_update()
        )

    @staticmethod
    async def add_song(
        db: AsyncSession,
        playlist_id: str,
        user_id: str,
        song_id: str,
        position: Optional[int] = None
    ) -> PlaylistSong:
        playlist = await PlaylistService.get_playlist(db, playlist_id, user_id)
        if not playlist or (playlist.user_id != user_id and not playlist.is_collaborative):
            raise PermissionError("Cannot modify this playlist.")

        await PlaylistService._lock_playlist(db, playlist_id)

        # Adding a song already present is a no-op rather than a duplicate row.
        existing_stmt = select(PlaylistSong).where(
            and_(PlaylistSong.playlist_id == playlist_id, PlaylistSong.song_id == song_id)
        )
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            if position is not None and position != existing.position:
                await PlaylistService._reindex(db, playlist_id, moved=existing, to_position=position)
                await db.commit()
                await db.refresh(existing)
            return existing

        if position is None:
            max_stmt = select(func.max(PlaylistSong.position)).where(
                PlaylistSong.playlist_id == playlist_id
            )
            current_max = (await db.execute(max_stmt)).scalar_one()
            position = 0 if current_max is None else int(current_max) + 1
            entry = PlaylistSong(playlist_id=playlist_id, song_id=song_id, position=position)
            db.add(entry)
        else:
            # An explicit position must shift everything at/after it, the same
            # way moving an existing entry does -- writing `position` straight
            # onto a new row (the previous behaviour) could collide with an
            # entry already at that position, since PlaylistSong has no
            # uniqueness constraint on (playlist_id, position) to catch it.
            entry = PlaylistSong(playlist_id=playlist_id, song_id=song_id, position=0)
            db.add(entry)
            await db.flush()
            await PlaylistService._reindex(db, playlist_id, moved=entry, to_position=position)
        await db.commit()
        await db.refresh(entry)

        # A playlist add is the second-strongest positive after a like: it is a
        # deliberate act of curation, not a passive listen.
        song = (await db.execute(select(Song).where(Song.id == song_id))).scalar_one_or_none()
        if song is not None and await SignalService.record_playlist_add(db, user_id, song):
            try:
                await db.commit()
            except Exception:
                # The PlaylistSong row is already durable; a signal failure must
                # not make a successful add look like an error.
                logger.exception("failed to commit playlist-add signal for %s", user_id)
                await db.rollback()
        return entry

    @staticmethod
    async def remove_song(
        db: AsyncSession,
        playlist_id: str,
        user_id: str,
        song_id: str
    ) -> bool:
        playlist = await PlaylistService.get_playlist(db, playlist_id, user_id)
        if not playlist or (playlist.user_id != user_id and not playlist.is_collaborative):
            raise PermissionError("Cannot modify this playlist.")

        await PlaylistService._lock_playlist(db, playlist_id)
        # song_id arrives unresolved from the URL path here (unlike add_song,
        # which is handed a resolved Song.id). A non-UUID cannot be in the
        # playlist, so there is nothing to delete -- same outcome as a valid id
        # that is absent, which this method already reports as success.
        if not is_uuid(song_id):
            return True
        stmt = delete(PlaylistSong).where(and_(PlaylistSong.playlist_id == playlist_id, PlaylistSong.song_id == song_id))
        await db.execute(stmt)

        # Close the gap so positions stay contiguous.
        await PlaylistService._compact_positions(db, playlist_id)
        await db.commit()
        return True

    @staticmethod
    async def _ordered_entries(db: AsyncSession, playlist_id: str) -> List[PlaylistSong]:
        stmt = (
            select(PlaylistSong)
            .where(PlaylistSong.playlist_id == playlist_id)
            .order_by(PlaylistSong.position, PlaylistSong.added_at)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def _compact_positions(db: AsyncSession, playlist_id: str) -> None:
        for idx, entry in enumerate(await PlaylistService._ordered_entries(db, playlist_id)):
            if entry.position != idx:
                entry.position = idx

    @staticmethod
    async def _reindex(
        db: AsyncSession,
        playlist_id: str,
        moved: PlaylistSong,
        to_position: int
    ) -> None:
        entries = [e for e in await PlaylistService._ordered_entries(db, playlist_id) if e.id != moved.id]
        target = max(0, min(int(to_position), len(entries)))
        entries.insert(target, moved)
        for idx, entry in enumerate(entries):
            entry.position = idx

    @staticmethod
    async def reorder_songs(
        db: AsyncSession,
        playlist_id: str,
        user_id: str,
        song_ids: List[str]
    ) -> bool:
        """
        Applies `song_ids` as the new leading order. Entries not named keep their
        existing relative order and follow on behind, so the result is always a
        contiguous 0..n-1 sequence with no duplicate positions.

        Raises KeyError if any given id is not in the playlist.
        """
        playlist = await PlaylistService.get_playlist(db, playlist_id, user_id)
        if not playlist or (playlist.user_id != user_id and not playlist.is_collaborative):
            raise PermissionError("Cannot modify this playlist.")

        await PlaylistService._lock_playlist(db, playlist_id)
        entries = await PlaylistService._ordered_entries(db, playlist_id)
        by_song = {e.song_id: e for e in entries}

        unknown = [s for s in song_ids if s not in by_song]
        if unknown:
            raise KeyError(f"Songs not in playlist: {', '.join(unknown)}")

        ordered: List[PlaylistSong] = []
        seen = set()
        for s_id in song_ids:
            if s_id in seen:
                continue
            seen.add(s_id)
            ordered.append(by_song[s_id])

        # Anything the caller omitted keeps its prior relative order at the end.
        ordered.extend(e for e in entries if e.song_id not in seen)

        for idx, entry in enumerate(ordered):
            entry.position = idx

        await db.commit()
        return True


playlist_service = PlaylistService()
