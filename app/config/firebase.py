import json
import logging
import base64
from typing import Optional, Dict, Any
import firebase_admin
from firebase_admin import credentials, auth
from app.config.settings import settings

logger = logging.getLogger("firebase")
_firebase_initialized = False


def init_firebase() -> bool:
    global _firebase_initialized
    if _firebase_initialized:
        return True

    try:
        if settings.FIREBASE_CREDENTIALS_PATH:
            cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred)
            _firebase_initialized = True
            logger.info("Firebase Admin initialized via credentials file.")
            return True

        if settings.FIREBASE_PROJECT_ID and settings.FIREBASE_CLIENT_EMAIL and settings.FIREBASE_PRIVATE_KEY:
            private_key = settings.FIREBASE_PRIVATE_KEY.replace("\\n", "\n")
            cred_dict = {
                "type": "service_account",
                "project_id": settings.FIREBASE_PROJECT_ID,
                "private_key": private_key,
                "client_email": settings.FIREBASE_CLIENT_EMAIL,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            _firebase_initialized = True
            logger.info("Firebase Admin initialized via environment variables.")
            return True

        if settings.FIREBASE_EMULATOR_ENABLED:
            logger.warning("Firebase Admin running in emulator/dev mode without live GCP credentials.")
            _firebase_initialized = False
            return True

        logger.warning("Firebase credentials not supplied and emulator is disabled.")
        return False
    except Exception as e:
        logger.error(f"Error initializing Firebase Admin: {e}")
        return False


def verify_firebase_token(token: str) -> Dict[str, Any]:
    """
    Verifies a Firebase ID token.
    In dev/emulator mode, accepts mock test tokens.
    """
    if settings.FIREBASE_EMULATOR_ENABLED and token.startswith("test_token_"):
        uid = token.replace("test_token_", "")
        return {
            "uid": uid,
            "email": f"{uid}@example.com",
            "name": f"User {uid}",
            "picture": f"https://api.dicebear.com/7.x/avataaars/svg?seed={uid}",
            "auth_time": 1700000000,
            "exp": 2000000000,
        }

    if _firebase_initialized:
        try:
            decoded = auth.verify_id_token(token)
            return decoded
        except Exception as e:
            if not settings.FIREBASE_EMULATOR_ENABLED:
                logger.warning(f"Firebase token verification failed: {e}")
                raise ValueError(f"Invalid Firebase ID token: {str(e)}")

    if settings.FIREBASE_EMULATOR_ENABLED:
        try:
            # Check if it's a test json or base64 token
            if token.startswith("mock_"):
                data = json.loads(base64.b64decode(token[5:]).decode())
                return data
        except Exception:
            pass

        # Default dev user fallback
        return {
            "uid": token if len(token) < 128 else token[:32],
            "email": "developer@musicapp.local",
            "name": "Dev User",
            "picture": "https://api.dicebear.com/7.x/avataaars/svg?seed=developer",
            "auth_time": 1700000000,
            "exp": 2000000000,
        }

    raise ValueError("Firebase Authentication is not configured.")


def delete_firebase_user(uid: str) -> bool:
    """Deletes a user from Firebase Auth if live Firebase is enabled."""
    if _firebase_initialized:
        try:
            auth.delete_user(uid)
            logger.info(f"Deleted Firebase user {uid}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete Firebase user {uid}: {e}")
            return False
    return True
