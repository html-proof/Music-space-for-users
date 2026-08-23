import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User, UserPreferences
from app.models.playback import CurrentPlayback
from app.models.recommendation import UserBehaviorProfile
from app.schemas.auth import SyncUserRequest
from app.config.firebase import delete_firebase_user

logger = logging.getLogger("auth_service")


class AuthService:
    @staticmethod
    async def get_user_by_firebase_uid(db: AsyncSession, firebase_uid: str) -> Optional[User]:
        stmt = select(User).where(User.firebase_uid == firebase_uid)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def sync_user(
        db: AsyncSession,
        firebase_payload: Dict[str, Any],
        sync_req: Optional[SyncUserRequest] = None
    ) -> User:
        firebase_uid = firebase_payload.get("uid")
        if not firebase_uid:
            raise ValueError("Firebase UID missing from token payload.")

        email = sync_req.email if sync_req and sync_req.email else firebase_payload.get("email")
        display_name = sync_req.display_name if sync_req and sync_req.display_name else firebase_payload.get("name")
        photo_url = sync_req.photo_url if sync_req and sync_req.photo_url else firebase_payload.get("picture")
        country = sync_req.country if sync_req and sync_req.country else "IN"
        language = sync_req.language if sync_req and sync_req.language else "English"

        user = await AuthService.get_user_by_firebase_uid(db, firebase_uid)

        now = datetime.now(timezone.utc)
        if user:
            # Update existing user
            user.last_login_at = now
            if email:
                user.email = email
            if display_name:
                user.display_name = display_name
            if photo_url:
                user.photo_url = photo_url
            if sync_req and sync_req.country:
                user.country = sync_req.country
            if sync_req and sync_req.language:
                user.language = sync_req.language
            await db.commit()
            await db.refresh(user)
            return user

        # Create new user
        user = User(
            firebase_uid=firebase_uid,
            email=email,
            display_name=display_name or f"User_{firebase_uid[:6]}",
            photo_url=photo_url,
            country=country,
            language=language,
            last_login_at=now
        )
        db.add(user)
        await db.flush()

        # Create default preferences
        preferences = UserPreferences(
            user_id=user.id,
            preferred_languages=[language, "Hindi"] if language != "Hindi" else ["Hindi", "English"],
            favorite_artists=[],
            favorite_genres=[],
            favorite_moods=[],
            explicit_content=True,
            audio_quality="very_high",
            autoplay=True,
            crossfade=0,
            data_saver=False
        )
        db.add(preferences)

        # Create initial playback state
        playback = CurrentPlayback(
            user_id=user.id,
            state="stopped",
            position_seconds=0.0,
            duration_seconds=0.0,
            volume=100,
            shuffle=False,
            repeat_mode="off",
            queue=[]
        )
        db.add(playback)

        # Create initial behavior profile
        behavior_profile = UserBehaviorProfile(
            user_id=user.id,
            top_artists=[],
            top_genres=[],
            top_languages=[language],
            top_moods=[],
            average_session_minutes=0.0,
            completion_rate=0.0,
            skip_rate=0.0,
            affinity_scores={}
        )
        db.add(behavior_profile)

        await db.commit()
        await db.refresh(user)
        logger.info(f"Created new user record for Firebase UID: {firebase_uid}")
        return user

    @staticmethod
    async def delete_user_account(db: AsyncSession, user: User) -> bool:
        firebase_uid = user.firebase_uid
        # Cascade delete in PostgreSQL
        await db.delete(user)
        await db.commit()

        # Delete from Firebase Auth
        delete_firebase_user(firebase_uid)
        logger.info(f"Purged user account for Firebase UID: {firebase_uid}")
        return True


auth_service = AuthService()
