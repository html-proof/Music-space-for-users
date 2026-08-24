import logging
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import is_uuid
from app.models.song import LikedSong, SavedAlbum, FollowedArtist, Song, Album, Artist
from app.services.cache_service import cache_service
from app.services.catalog_queue import catalog_queue
from app.services.signal_service import SignalService
from app.utils.cache_keys import home_recommendations_key

logger = logging.getLogger("library_service")


class LibraryService:
    @staticmethod
    async def is_song_liked(db: AsyncSession, user_id: str, song_id: str) -> bool:
        if not is_uuid(song_id):
            return False
        stmt = select(LikedSong).where(and_(LikedSong.user_id == user_id, LikedSong.song_id == song_id))
        res = await db.execute(stmt)
        return res.scalar_one_or_none() is not None

    @staticmethod
    async def like_song(db: AsyncSession, user_id: str, song_id: str) -> bool:
        # Callers pass a resolved Song.id, but guard anyway: LikedSong.song_id
        # is a uuid FK and an unparseable value would raise at bind time.
        if not is_uuid(song_id):
            return False
        # The song row may still be sitting in the catalog write queue, in
        # which case this id has no row behind it yet and the FK insert below
        # would fail. Usually a no-op: the flusher has normally already run.
        await catalog_queue.ensure_persisted(db, song_id)
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
            # Likes are weighted heavily by the recommender.
            await LibraryService._signal_like(db, user_id, song_id, liked=True)
            await cache_service.delete(home_recommendations_key(user_id))
        return True

    @staticmethod
    async def _signal_like(db: AsyncSession, user_id: str, song_id: str, liked: bool) -> None:
        """Fan a like/unlike out to the entity signal log.

        The log is append-only, so an unlike is recorded as an equal negative
        rather than by deleting the original row -- summing the log then gives the
        current affinity. Failures are swallowed: the LikedSong row is the record
        of truth and is already committed.
        """
        song = (await db.execute(select(Song).where(Song.id == song_id))).scalar_one_or_none()
        if song is None:
            return
        if await SignalService.record_like(db, user_id, song, liked=liked):
            try:
                await db.commit()
            except Exception:
                logger.exception("failed to commit like signal for %s", user_id)
                await db.rollback()

    @staticmethod
    async def unlike_song(db: AsyncSession, user_id: str, song_id: str) -> bool:
        # song_id may be the raw path value when the song is unknown to us
        # (see app/api/library.py), so it is not necessarily a UUID.
        if not is_uuid(song_id):
            return False
        stmt = delete(LikedSong).where(and_(LikedSong.user_id == user_id, LikedSong.song_id == song_id))
        await db.execute(stmt)
        await db.commit()
        await LibraryService._signal_like(db, user_id, song_id, liked=False)
        await cache_service.delete(home_recommendations_key(user_id))
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
        # album_id is the raw path value -- nothing resolves it first, so it may
        # be a Gaana key rather than one of our uuids. Report failure instead of
        # letting it reach the uuid FK column.
        if not is_uuid(album_id):
            return False
        await catalog_queue.ensure_persisted(db, album_id)
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
            await LibraryService._commit_signals(
                db, user_id, await SignalService.record_save_album(db, user_id, album_id)
            )
        return True

    @staticmethod
    async def _commit_signals(db: AsyncSession, user_id: str, written: int) -> None:
        """Commit signal rows written after their action already committed.

        Failures are swallowed: the library row is the record of truth and is
        durable by this point, so a signal-log problem must not surface as a
        failed save or follow.
        """
        if not written:
            return
        try:
            await db.commit()
        except Exception:
            logger.exception("failed to commit %d library signals for %s", written, user_id)
            await db.rollback()

    @staticmethod
    async def unsave_album(db: AsyncSession, user_id: str, album_id: str) -> bool:
        if not is_uuid(album_id):
            return False
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
        # Same as save_album: unresolved path value against a uuid FK column.
        if not is_uuid(artist_id):
            return False
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
            # The artist's name is recorded alongside the id: song rows carry a
            # credit string, not an artist_id, so a name-keyed signal is what the
            # feed can actually match against.
            artist = (await db.execute(select(Artist).where(Artist.id == artist_id))).scalar_one_or_none()
            await LibraryService._commit_signals(
                db, user_id,
                await SignalService.record_follow_artist(
                    db, user_id, artist_id, artist_name=(artist.name if artist else "")
                ),
            )
        return True

    @staticmethod
    async def unfollow_artist(db: AsyncSession, user_id: str, artist_id: str) -> bool:
        if not is_uuid(artist_id):
            return False
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
