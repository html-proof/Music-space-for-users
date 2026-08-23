import logging
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.song import LikedSong, SavedAlbum, FollowedArtist, Song, Album, Artist

logger = logging.getLogger("library_service")


class LibraryService:
    @staticmethod
    async def is_song_liked(db: AsyncSession, user_id: str, song_id: str) -> bool:
        stmt = select(LikedSong).where(and_(LikedSong.user_id == user_id, LikedSong.song_id == song_id))
        res = await db.execute(stmt)
        return res.scalar_one_or_none() is not None

    @staticmethod
    async def like_song(db: AsyncSession, user_id: str, song_id: str) -> bool:
        stmt = select(LikedSong).where(and_(LikedSong.user_id == user_id, LikedSong.song_id == song_id))
        res = await db.execute(stmt)
        liked = res.scalar_one_or_none()
        if not liked:
            liked = LikedSong(
                user_id=user_id,
                song_id=song_id,
                liked_at=datetime.now(timezone.utc)
            )
            db.add(liked)
            await db.commit()
        return True

    @staticmethod
    async def unlike_song(db: AsyncSession, user_id: str, song_id: str) -> bool:
        stmt = delete(LikedSong).where(and_(LikedSong.user_id == user_id, LikedSong.song_id == song_id))
        await db.execute(stmt)
        await db.commit()
        return True

    @staticmethod
    async def get_liked_songs(
        db: AsyncSession,
        user_id: str,
        limit: int = 20,
        offset: int = 0
    ) -> List[Song]:
        stmt = (
            select(Song)
            .join(LikedSong, LikedSong.song_id == Song.id)
            .where(LikedSong.user_id == user_id)
            .order_by(LikedSong.liked_at.desc())
            .offset(offset)
            .limit(limit)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def save_album(db: AsyncSession, user_id: str, album_id: str) -> bool:
        stmt = select(SavedAlbum).where(and_(SavedAlbum.user_id == user_id, SavedAlbum.album_id == album_id))
        res = await db.execute(stmt)
        saved = res.scalar_one_or_none()
        if not saved:
            saved = SavedAlbum(
                user_id=user_id,
                album_id=album_id,
                saved_at=datetime.now(timezone.utc)
            )
            db.add(saved)
            await db.commit()
        return True

    @staticmethod
    async def unsave_album(db: AsyncSession, user_id: str, album_id: str) -> bool:
        stmt = delete(SavedAlbum).where(and_(SavedAlbum.user_id == user_id, SavedAlbum.album_id == album_id))
        await db.execute(stmt)
        await db.commit()
        return True

    @staticmethod
    async def get_saved_albums(
        db: AsyncSession,
        user_id: str,
        limit: int = 20,
        offset: int = 0
    ) -> List[Album]:
        stmt = (
            select(Album)
            .join(SavedAlbum, SavedAlbum.album_id == Album.id)
            .where(SavedAlbum.user_id == user_id)
            .order_by(SavedAlbum.saved_at.desc())
            .offset(offset)
            .limit(limit)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def follow_artist(db: AsyncSession, user_id: str, artist_id: str) -> bool:
        stmt = select(FollowedArtist).where(and_(FollowedArtist.user_id == user_id, FollowedArtist.artist_id == artist_id))
        res = await db.execute(stmt)
        followed = res.scalar_one_or_none()
        if not followed:
            followed = FollowedArtist(
                user_id=user_id,
                artist_id=artist_id,
                followed_at=datetime.now(timezone.utc)
            )
            db.add(followed)
            await db.commit()
        return True

    @staticmethod
    async def unfollow_artist(db: AsyncSession, user_id: str, artist_id: str) -> bool:
        stmt = delete(FollowedArtist).where(and_(FollowedArtist.user_id == user_id, FollowedArtist.artist_id == artist_id))
        await db.execute(stmt)
        await db.commit()
        return True

    @staticmethod
    async def get_followed_artists(
        db: AsyncSession,
        user_id: str,
        limit: int = 20,
        offset: int = 0
    ) -> List[Artist]:
        stmt = (
            select(Artist)
            .join(FollowedArtist, FollowedArtist.artist_id == Artist.id)
            .where(FollowedArtist.user_id == user_id)
            .order_by(FollowedArtist.followed_at.desc())
            .offset(offset)
            .limit(limit)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())


library_service = LibraryService()
