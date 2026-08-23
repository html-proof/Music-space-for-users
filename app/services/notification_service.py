import logging
from datetime import datetime, timezone
from typing import Any, Dict, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config.firebase import is_firebase_initialized
from app.models.device import Device
from app.models.notification import Notification, NotificationPreferences
from app.models.song import Artist, FollowedArtist, Song
from app.schemas.notification import NotificationPreferencesUpdate

logger = logging.getLogger("notification_service")

# FCM's own cap on a single multicast call.
_FCM_BATCH_SIZE = 500


def _serialize(n: Notification) -> Dict[str, Any]:
    return {
        "id": n.id,
        "category": n.category,
        "title": n.title,
        "body": n.body,
        "song_id": n.song_id,
        "artist_id": n.artist_id,
        "sent_at": n.sent_at.isoformat() if n.sent_at else "",
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "is_read": n.read_at is not None,
    }


class NotificationService:
    @staticmethod
    async def get_preferences(db: AsyncSession, user_id: str) -> NotificationPreferences:
        stmt = select(NotificationPreferences).where(NotificationPreferences.user_id == user_id)
        result = await db.execute(stmt)
        prefs = result.scalar_one_or_none()
        if not prefs:
            prefs = NotificationPreferences(user_id=user_id)
            db.add(prefs)
            await db.commit()
            await db.refresh(prefs)
        return prefs

    @staticmethod
    async def update_preferences(
        db: AsyncSession, user_id: str, updates: NotificationPreferencesUpdate
    ) -> NotificationPreferences:
        prefs = await NotificationService.get_preferences(db, user_id)
        for key, value in updates.model_dump(exclude_unset=True).items():
            setattr(prefs, key, value)
        await db.commit()
        await db.refresh(prefs)
        return prefs

    @staticmethod
    async def list_notifications(
        db: AsyncSession, user_id: str, limit: int = 30, offset: int = 0
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.sent_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return [_serialize(n) for n in result.scalars().all()]

    @staticmethod
    async def mark_read(db: AsyncSession, user_id: str, notification_id: str) -> bool:
        stmt = select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user_id
        )
        result = await db.execute(stmt)
        notification = result.scalar_one_or_none()
        if not notification:
            return False
        if notification.read_at is None:
            notification.read_at = datetime.now(timezone.utc)
            await db.commit()
        return True

    @staticmethod
    async def notify_new_song(db: AsyncSession, song: Song) -> int:
        """
        Notify every user following `song`'s artist that it's out, respecting
        each user's `new_release_songs` preference. Writes a Notification row
        for every eligible follower regardless of whether they have a push
        token (that's the in-app inbox); only sends an actual FCM push to
        followers with a registered device token.

        Best-effort throughout: a notification failure must never surface as
        a failure of the release-discovery pass that called this. Returns the
        number of followers notified (rows written), for the caller's log line.
        """
        if not song.artist_id:
            return 0

        stmt = (
            select(FollowedArtist.user_id)
            .join(NotificationPreferences, NotificationPreferences.user_id == FollowedArtist.user_id, isouter=True)
            .where(
                FollowedArtist.artist_id == song.artist_id,
                # No preferences row yet means the default (True) applies.
                (NotificationPreferences.new_release_songs.is_(True))
                | (NotificationPreferences.new_release_songs.is_(None)),
            )
        )
        try:
            follower_ids = list((await db.execute(stmt)).scalars().all())
        except Exception:
            logger.exception("failed to look up followers for artist %s", song.artist_id)
            return 0

        if not follower_ids:
            return 0

        artist = (await db.execute(select(Artist).where(Artist.id == song.artist_id))).scalar_one_or_none()
        artist_name = artist.name if artist else song.artist_name
        title = f"New release from {artist_name}"
        body = f'"{song.title}" is now available. Tap to listen.'

        notifications = [
            Notification(
                user_id=user_id,
                category="new_release_song",
                title=title,
                body=body,
                song_id=song.id,
                artist_id=song.artist_id,
            )
            for user_id in follower_ids
        ]
        db.add_all(notifications)
        try:
            await db.commit()
        except Exception:
            logger.exception("failed to persist new-release notifications for song %s", song.id)
            await db.rollback()
            return 0

        await NotificationService._send_push(
            db, follower_ids, title=title, body=body,
            data={"type": "new_release_song", "song_id": song.id, "artist_id": song.artist_id or ""},
        )
        return len(notifications)

    @staticmethod
    async def _send_push(
        db: AsyncSession,
        user_ids: List[str],
        title: str,
        body: str,
        data: Dict[str, str],
    ) -> None:
        if not is_firebase_initialized():
            logger.info("Firebase not initialized; skipping push send (%d recipients)", len(user_ids))
            return

        stmt = select(Device.push_token).where(
            Device.user_id.in_(user_ids), Device.push_token.is_not(None)
        )
        try:
            tokens = [t for t in (await db.execute(stmt)).scalars().all() if t]
        except Exception:
            logger.exception("failed to look up push tokens")
            return
        if not tokens:
            return

        try:
            from firebase_admin import messaging
        except Exception:
            logger.exception("firebase_admin.messaging unavailable; skipping push send")
            return

        for i in range(0, len(tokens), _FCM_BATCH_SIZE):
            batch = tokens[i:i + _FCM_BATCH_SIZE]
            message = messaging.MulticastMessage(
                tokens=batch,
                notification=messaging.Notification(title=title, body=body),
                data=data,
            )
            try:
                response = messaging.send_each_for_multicast(message)
                logger.info(
                    "push batch sent: %d success, %d failure",
                    response.success_count, response.failure_count,
                )
            except Exception:
                logger.exception("FCM send failed for a batch of %d tokens", len(batch))


notification_service = NotificationService()
