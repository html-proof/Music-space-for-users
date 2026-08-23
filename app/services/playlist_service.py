import logging
from typing import List, Optional
from sqlalchemy import select, and_, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.playlist import Playlist, PlaylistSong
from app.models.song import Song
from app.schemas.playlist import PlaylistCreate, PlaylistUpdate

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
        stmt = select(Playlist).where(Playlist.id == playlist_id)
        res = await db.execute(stmt)
        playlist = res.scalar_one_or_none()
        if not playlist:
            return None
        # Check permissions: if private, must belong to user
        if not playlist.is_public and playlist.user_id != user_id:
            return None
        return playlist

    @staticmethod
    async def update_playlist(
        db: AsyncSession,
        playlist_id: str,
        user_id: str,
        req: PlaylistUpdate
    ) -> Optional[Playlist]:
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
        stmt = select(Playlist).where(and_(Playlist.id == playlist_id, Playlist.user_id == user_id))
        res = await db.execute(stmt)
        playlist = res.scalar_one_or_none()
        if not playlist:
            return False

        await db.delete(playlist)
        await db.commit()
        return True

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

        # Determine position
        if position is None:
            count_stmt = select(func.count(PlaylistSong.id)).where(PlaylistSong.playlist_id == playlist_id)
            count_res = await db.execute(count_stmt)
            position = count_res.scalar_one() or 0

        entry = PlaylistSong(
            playlist_id=playlist_id,
            song_id=song_id,
            position=position
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
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

        stmt = delete(PlaylistSong).where(and_(PlaylistSong.playlist_id == playlist_id, PlaylistSong.song_id == song_id))
        await db.execute(stmt)
        await db.commit()
        return True

    @staticmethod
    async def reorder_songs(
        db: AsyncSession,
        playlist_id: str,
        user_id: str,
        song_ids: List[str]
    ) -> bool:
        playlist = await PlaylistService.get_playlist(db, playlist_id, user_id)
        if not playlist or (playlist.user_id != user_id and not playlist.is_collaborative):
            raise PermissionError("Cannot modify this playlist.")

        stmt = select(PlaylistSong).where(PlaylistSong.playlist_id == playlist_id)
        res = await db.execute(stmt)
        entries = {e.song_id: e for e in res.scalars().all()}

        for idx, s_id in enumerate(song_ids):
            if s_id in entries:
                entries[s_id].position = idx

        await db.commit()
        return True


playlist_service = PlaylistService()
